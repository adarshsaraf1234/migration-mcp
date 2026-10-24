"""Unit tests for the Python-to-JavaScript Graphify context boundary."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from get_migration_context import get_migration_context


class GetMigrationContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.graph = self.root / "graph.json"
        self.graph.write_text("{}", encoding="utf-8")
        self.bridge = Path(__file__).resolve().parents[1] / "graph_context_bridge.mjs"
        self.project = Path(__file__).resolve().parents[3]

    def invoke(self, result=None, **options):
        process = Mock(return_value=result or SimpleNamespace(returncode=0, stdout="{}", stderr=""))
        output = get_migration_context(
            str(self.graph),
            "lookup",
            bridge_path=self.bridge,
            project_root=self.project,
            node_executable=sys.executable,
            **options,
            run_process=process,
        )
        return output, process

    @staticmethod
    def adapter_context():
        target = {"id": "lookup", "label": "findById()", "source_file": "legacy-utils.js", "source_location": "L2"}
        caller = {"id": "create", "label": "createOrder()", "source_file": "order-service.js"}
        call_edge = {"source": "create", "target": "lookup", "relation": "calls", "source_file": "order-service.js", "source_location": "L7", "confidence": "EXTRACTED"}
        import_node = {"id": "order-file", "label": "order-service.js"}
        import_edge = {"source": "order-file", "target": "lookup", "relation": "imports"}
        return {
            "graph": {"path": "graph.json", "graphify_version": "0.9.94", "node_count": 2, "edge_count": 2},
            "target": target,
            "dependencies": [],
            "direct_dependents": [
                {"node": caller, "edge": call_edge},
                {"node": import_node, "edge": import_edge},
            ],
            "callers": [{"node": caller, "edge": call_edge}],
            "impact": {"maxDepth": 3, "relations": ["calls", "imports", "imports_from"], "direct": [], "transitive": [], "truncated": False},
            "evidence": {"source_references": ["legacy-utils.js", "order-service.js"], "caller_edges": [call_edge]},
            "warnings": ["One edge has unresolved endpoints."],
        }

    def bridge_response(self, body):
        return SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr="")

    def test_success_reuses_adapter_context_and_keeps_direction_evidence(self) -> None:
        context = self.adapter_context()
        output, process = self.invoke(self.bridge_response({"ok": True, "context": context}), max_depth=4, max_nodes=20, max_edges=12)

        self.assertTrue(output["success"])
        self.assertEqual(output["target"]["id"], "lookup")
        self.assertEqual([item["node"]["id"] for item in output["callers"]], ["create"])
        self.assertEqual([item["edge"]["relation"] for item in output["import_dependents"]], ["imports"])
        self.assertEqual(output["callers"][0]["edge"]["source"], "create")
        self.assertEqual(output["callers"][0]["edge"]["target"], "lookup")
        self.assertEqual(output["warnings"], context["warnings"])
        self.assertFalse(output["evidence_completeness"]["complete"])
        args, kwargs = process.call_args
        self.assertEqual(args[0], [str(Path(sys.executable).resolve()), str(self.bridge.resolve())])
        self.assertEqual(kwargs["cwd"], str(self.project.resolve()))
        sent = json.loads(kwargs["input"])
        self.assertEqual(sent["options"], {"maxDepth": 4, "maxNodes": 20, "maxEdges": 12})
        self.assertEqual(kwargs["timeout"], 30)

    def test_target_not_found_diagnostic_is_preserved(self) -> None:
        output, _ = self.invoke(self.bridge_response({
            "ok": False,
            "error": {"code": "NODE_NOT_FOUND", "message": "No graph node matches.", "details": {"target": "lookup"}},
        }))
        self.assertFalse(output["success"])
        self.assertEqual(output["error"]["code"], "NODE_NOT_FOUND")

    def test_ambiguous_target_candidates_are_preserved(self) -> None:
        candidates = [{"id": "a", "label": "findById()"}, {"id": "b", "label": "findById()"}]
        output, _ = self.invoke(self.bridge_response({
            "ok": False,
            "error": {"code": "AMBIGUOUS_TARGET", "message": "Matches multiple nodes.", "details": {"candidates": candidates}},
        }))
        self.assertFalse(output["success"])
        self.assertEqual(output["error"]["details"]["candidates"], candidates)

    def test_invalid_graph_diagnostic_is_preserved(self) -> None:
        output, _ = self.invoke(self.bridge_response({
            "ok": False,
            "error": {"code": "INVALID_GRAPH", "message": "Graph must contain links or edges."},
        }))
        self.assertEqual(output["error"]["code"], "INVALID_GRAPH")

    def test_missing_graph_file_does_not_start_node(self) -> None:
        process = Mock()
        output = get_migration_context(
            str(self.root / "missing.json"), "lookup", run_process=process,
        )
        self.assertFalse(output["success"])
        self.assertEqual(output["error"]["code"], "GRAPH_NOT_FOUND")
        process.assert_not_called()

    def test_traversal_and_result_limits_are_forwarded_and_strictly_bounded(self) -> None:
        response = self.bridge_response({"ok": True, "context": self.adapter_context()})
        output, process = self.invoke(response, max_depth=8, max_nodes=200, max_edges=200)
        self.assertTrue(output["success"])
        self.assertEqual(json.loads(process.call_args.kwargs["input"])["options"], {
            "maxDepth": 8, "maxNodes": 200, "maxEdges": 200,
        })
        output, process = self.invoke(max_depth=9)
        self.assertFalse(output["success"])
        self.assertEqual(output["error"]["code"], "INVALID_OPTION")
        process.assert_not_called()

    def test_node_failure_is_reported(self) -> None:
        output, _ = self.invoke(SimpleNamespace(returncode=7, stdout="", stderr="node failed"))
        self.assertFalse(output["success"])
        self.assertEqual(output["error"]["code"], "NODE_FAILED")
        self.assertEqual(output["error"]["exit_code"], 7)

    def test_malformed_bridge_json_and_shape_are_reported(self) -> None:
        output, _ = self.invoke(SimpleNamespace(returncode=0, stdout="not json", stderr=""))
        self.assertEqual(output["error"]["code"], "MALFORMED_BRIDGE_OUTPUT")
        output, _ = self.invoke(SimpleNamespace(returncode=0, stdout='{"unexpected":true}', stderr=""))
        self.assertEqual(output["error"]["code"], "MALFORMED_BRIDGE_OUTPUT")

    def test_timeout_is_reported(self) -> None:
        process = Mock(side_effect=subprocess.TimeoutExpired(["node", "bridge"], 11))
        output = get_migration_context(
            str(self.graph), "lookup", bridge_path=self.bridge,
            project_root=self.project, node_executable=sys.executable,
            timeout_seconds=11, run_process=process,
        )
        self.assertEqual(output["error"]["code"], "TIMEOUT")


if __name__ == "__main__":
    unittest.main()
