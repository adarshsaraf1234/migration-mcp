"""SDK-backed stdio smoke tests for the Migration MCP scaffold."""

import sys
import unittest
from pathlib import Path

from mcp import Client, StdioServerParameters


SERVER_FILE = Path(__file__).resolve().parents[1] / "server.py"


class MigrationMCPServerSmokeTest(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_server_registers_and_serves_health_tool(self) -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(SERVER_FILE)],
        )

        async with Client(params, raise_exceptions=True) as client:
            listed = await client.list_tools()
            self.assertCountEqual(
                [tool.name for tool in listed.tools],
                ["analyze_legacy_repo", "compare_migration_graphs", "get_migration_context", "health"],
            )

            response = await client.call_tool("health", {})
            self.assertEqual(len(response.content), 1)
            self.assertEqual(response.content[0].text, "ok")

            context_response = await client.call_tool(
                "get_migration_context",
                {"graph_path": "/migration-mcp-test-missing/graph.json", "target": "lookup"},
            )
            tool_result = context_response.structured_content
            self.assertIsNotNone(tool_result)
            self.assertFalse(tool_result["success"])
            self.assertEqual(tool_result["error"]["code"], "GRAPH_NOT_FOUND")

            compare_response = await client.call_tool(
                "compare_migration_graphs",
                {
                    "baseline_graph_path": "/migration-mcp-test-missing/baseline.json",
                    "after_graph_path": "/migration-mcp-test-missing/after.json",
                },
            )
            comparison_error = compare_response.structured_content
            self.assertIsNotNone(comparison_error)
            self.assertFalse(comparison_error["success"])
            self.assertEqual(comparison_error["error"]["code"], "GRAPH_NOT_FOUND")

            remote_error_response = await client.call_tool(
                "analyze_legacy_repo",
                {"repository_url": "https://example.com/not-github/repo"},
            )
            remote_error = remote_error_response.structured_content
            self.assertIsNotNone(remote_error)
            self.assertFalse(remote_error["success"])
            self.assertEqual(remote_error["error"]["code"], "INVALID_REPOSITORY_URL")

            missing_target_response = await client.call_tool("analyze_legacy_repo", {})
            missing_target = missing_target_response.structured_content
            self.assertIsNotNone(missing_target)
            self.assertFalse(missing_target["success"])
            self.assertEqual(missing_target["error"]["code"], "INVALID_REPOSITORY_INPUT")


if __name__ == "__main__":
    unittest.main()
