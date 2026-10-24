"""Protocol and bearer-token checks for hosted Streamable HTTP mode."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from http_transport import create_http_app
from server import server
from starlette.testclient import TestClient


TOKEN = "test-token-" + "x" * 40


class HttpTransportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client_context = TestClient(create_http_app(server, TOKEN, ["testserver"]))
        self.client = self.client_context.__enter__()
        self.addCleanup(self.client_context.__exit__, None, None, None)
        self.headers = {
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }

    def test_requires_long_token_and_host_allowlist(self):
        with self.assertRaises(ValueError):
            create_http_app(server, "short", ["testserver"])
        with self.assertRaises(ValueError):
            create_http_app(server, TOKEN, [])

    def test_health_is_available_and_mcp_requires_bearer_token(self):
        self.assertEqual(self.client.get("/healthz").json(), {"status": "ok"})
        response = self.client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["www-authenticate"], "Bearer")

    def test_streamable_http_discovers_registered_tools_with_valid_token(self):
        initialized = self.client.post(
            "/mcp",
            headers=self.headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "unit-test", "version": "1"},
                },
            },
        )
        self.assertEqual(initialized.status_code, 200)
        tools = self.client.post(
            "/mcp",
            headers=self.headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        self.assertEqual(tools.status_code, 200)
        names = [tool["name"] for tool in tools.json()["result"]["tools"]]
        self.assertCountEqual(
            names,
            ["health", "analyze_legacy_repo", "get_migration_context", "compare_migration_graphs"],
        )

    def test_host_allowlist_is_enforced_by_mcp_sdk(self):
        response = self.client.post(
            "/mcp",
            headers=self.headers | {"Host": "unexpected.example"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        self.assertEqual(response.status_code, 421)


if __name__ == "__main__":
    unittest.main()
