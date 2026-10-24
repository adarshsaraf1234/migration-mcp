"""End-to-end MCP workflow against a temporary repository and real Graphify.

Run explicitly with MIGRATION_MCP_RUN_GRAPHIFY_INTEGRATION=1. The test writes
only inside its TemporaryDirectory and starts the actual MCP server over stdio.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import Client, StdioServerParameters


SERVER_FILE = Path(__file__).resolve().parents[1] / "server.py"


def _configured_executable(variable: str, default: str) -> str | None:
    configured = os.environ.get(variable, default)
    if os.path.sep in configured:
        candidate = Path(configured).expanduser()
        return str(candidate.resolve()) if candidate.is_file() and os.access(candidate, os.X_OK) else None
    return shutil.which(configured)


class MigrationMCPGraphifyWorkflowIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_analyze_context_baseline_and_compare_over_stdio(self) -> None:
        if os.environ.get("MIGRATION_MCP_RUN_GRAPHIFY_INTEGRATION") != "1":
            self.skipTest(
                "Real Graphify workflow is opt-in; set MIGRATION_MCP_RUN_GRAPHIFY_INTEGRATION=1."
            )

        node = _configured_executable("MIGRATION_MCP_NODE", "node")
        graphify = _configured_executable(
            "MIGRATION_MCP_GRAPHIFY",
            str(Path.home() / ".local" / "bin" / "graphify"),
        )
        if not node:
            self.skipTest(
                "Node.js is unavailable; install Node.js or set MIGRATION_MCP_NODE to its executable."
            )
        if not graphify or Path(graphify).name != "graphify":
            self.skipTest(
                "Graphify is unavailable; install the Graphify CLI or set "
                "MIGRATION_MCP_GRAPHIFY to an executable named 'graphify'."
            )

        with tempfile.TemporaryDirectory(prefix="migration-mcp-e2e-") as temporary:
            temporary_root = Path(temporary)
            target = temporary_root / "legacy-app"
            target.mkdir()
            (target / "package.json").write_text(
                json.dumps({"name": "temporary-legacy-app", "private": True, "type": "module"}),
                encoding="utf-8",
            )
            (target / "legacy-utils.js").write_text(
                """export function findById(records, id, callback) {
  process.nextTick(() => {
    const record = records.find((item) => item.id === id);
    if (!record) return callback(new Error(`Record not found: ${id}`));
    callback(null, record);
  });
}
""",
                encoding="utf-8",
            )
            (target / "order-service.js").write_text(
                """import { findById } from './legacy-utils.js';

export function createOrder(records, id, callback) {
  findById(records, id, (error, record) => {
    if (error) return callback(error);
    callback(null, { recordId: record.id });
  });
}
""",
                encoding="utf-8",
            )

            server_env = {
                **os.environ,
                "MIGRATION_MCP_NODE": node,
                "MIGRATION_MCP_GRAPHIFY": graphify,
            }
            params = StdioServerParameters(
                command=sys.executable,
                args=[str(SERVER_FILE)],
                env=server_env,
            )

            async with Client(params, raise_exceptions=True) as client:
                listed = await client.list_tools()
                self.assertCountEqual(
                    [tool.name for tool in listed.tools],
                    ["analyze_legacy_repo", "compare_migration_graphs", "get_migration_context", "health"],
                )

                # A successful handshake and subsequent structured calls confirm
                # stderr diagnostics are not leaking into the stdout protocol.
                health = await client.call_tool("health", {})
                self.assertEqual(health.content[0].text, "ok")

                analyzed = (await client.call_tool(
                    "analyze_legacy_repo",
                    {"repository_path": str(target), "timeout_seconds": 180},
                )).structured_content
                self.assertIsNotNone(analyzed)
                self.assertTrue(analyzed["success"], analyzed)
                graph_path = Path(analyzed["artifacts"]["graph_json"])
                self.assertEqual(Path(analyzed["target_repository"]), target.resolve())
                for artifact in analyzed["artifacts"].values():
                    artifact_path = Path(artifact)
                    self.assertTrue(artifact_path.is_file(), artifact_path)
                    self.assertTrue(artifact_path.is_relative_to(target.resolve()))

                invalid_analysis = (await client.call_tool(
                    "analyze_legacy_repo",
                    {"repository_path": str(target / "missing-repository")},
                )).structured_content
                self.assertFalse(invalid_analysis["success"])
                self.assertEqual(invalid_analysis["error"]["code"], "repository_not_found")

                native_before = json.loads(graph_path.read_text(encoding="utf-8"))
                nodes_before = native_before.get("nodes", [])
                target_nodes = [
                    node_record
                    for node_record in nodes_before
                    if isinstance(node_record, dict)
                    and node_record.get("label") in {"findById", "findById()"}
                ]
                self.assertEqual(len(target_nodes), 1, "Expected one extracted findById function node")
                target_id = target_nodes[0].get("id")
                self.assertIsInstance(target_id, str)
                context = (await client.call_tool(
                    "get_migration_context",
                    {"graph_path": str(graph_path), "target": target_id, "max_depth": 2},
                )).structured_content
                self.assertTrue(context["success"], context)
                self.assertEqual(context["target"].get("id"), target_id)
                self.assertTrue(context["callers"], "Expected createOrder to call findById")
                self.assertFalse(context["evidence_completeness"]["complete"])

                invalid_context = (await client.call_tool(
                    "get_migration_context",
                    {"graph_path": str(graph_path), "target": "unknown-migration-symbol"},
                )).structured_content
                self.assertFalse(invalid_context["success"])
                self.assertEqual(invalid_context["error"]["code"], "NODE_NOT_FOUND")

                invalid_limits = (await client.call_tool(
                    "get_migration_context",
                    {"graph_path": str(graph_path), "target": target_id, "max_depth": 999},
                )).structured_content
                self.assertFalse(invalid_limits["success"])
                self.assertEqual(invalid_limits["error"]["code"], "INVALID_OPTION")

                baseline_path = temporary_root / "baseline-graph.json"
                shutil.copyfile(graph_path, baseline_path)
                baseline_hash = hashlib.sha256(baseline_path.read_bytes()).hexdigest()

                # Introduce a real additional caller in the isolated test repo,
                # then let the normal runner regenerate its native artifacts.
                (target / "report.js").write_text(
                    """import { findById } from './legacy-utils.js';

export function describeProduct(records, id, callback) {
  findById(records, id, (error, record) => {
    if (error) return callback(error);
    callback(null, record.name);
  });
}
""",
                    encoding="utf-8",
                )
                analyzed_after = (await client.call_tool(
                    "analyze_legacy_repo",
                    {"repository_path": str(target), "timeout_seconds": 180},
                )).structured_content
                self.assertTrue(analyzed_after["success"], analyzed_after)
                after_path = Path(analyzed_after["artifacts"]["graph_json"])
                self.assertEqual(hashlib.sha256(baseline_path.read_bytes()).hexdigest(), baseline_hash)

                compared = (await client.call_tool(
                    "compare_migration_graphs",
                    {
                        "baseline_graph_path": str(baseline_path),
                        "after_graph_path": str(after_path),
                    },
                )).structured_content
                self.assertTrue(compared["success"], compared)
                diff = compared["comparison"]
                self.assertEqual(compared["behavioral_equivalence"], "not_assessed")
                added_labels = {
                    (item.get("node") or item.get("after") or {}).get("label")
                    for item in diff["nodes"]["added"]
                    if isinstance(item, dict)
                }
                self.assertTrue(
                    any(label in {"describeProduct", "describeProduct()"} for label in added_labels),
                    f"Expected describeProduct node in additions; got {diff['nodes']['added']!r}",
                )
                added_call_edges = [
                    item for item in diff["edges"]["added"]
                    if isinstance(item, dict)
                    and (item.get("edge") or item.get("after") or {}).get("relation") == "calls"
                ]
                self.assertTrue(
                    added_call_edges,
                    f"Expected a new directed calls relationship; got {diff['edges']['added']!r}",
                )

                invalid_comparison = (await client.call_tool(
                    "compare_migration_graphs",
                    {
                        "baseline_graph_path": str(baseline_path),
                        "after_graph_path": str(temporary_root / "missing-after-graph.json"),
                    },
                )).structured_content
                self.assertFalse(invalid_comparison["success"])
                self.assertEqual(invalid_comparison["error"]["code"], "GRAPH_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
