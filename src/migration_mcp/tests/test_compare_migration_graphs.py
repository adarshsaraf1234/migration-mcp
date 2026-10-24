"""Unit and adapter-backed tests for the graph comparison MCP boundary."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compare_migration_graphs import compare_migration_graphs


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_DIR = REPOSITORY_ROOT / "tests" / "fixtures"
BASELINE_FIXTURE = FIXTURE_DIR / "graph-diff-baseline.json"
AFTER_FIXTURE = FIXTURE_DIR / "graph-diff-after.json"
BRIDGE = Path(__file__).resolve().parents[1] / "graph_diff_bridge.mjs"


def _empty_diff():
    return {
        "schema_version": 1,
        "baseline": {"path": "baseline.json"},
        "after": {"path": "after.json"},
        "summary": {
            "nodes_added": 0, "nodes_removed": 0, "nodes_changed": 0,
            "edges_added": 0, "edges_removed": 0, "edges_changed": 0,
            "ambiguous_node_matches": 0, "complete": True,
        },
        "nodes": {"added": [], "removed": [], "changed": [], "ambiguous": []},
        "edges": {"added": [], "removed": [], "changed": []},
        "warnings": [],
        "limitations": ["Structural comparison only."],
    }


class CompareMigrationGraphsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.baseline = self.root / "baseline.json"
        self.after = self.root / "after.json"
        self.baseline.write_text("{}", encoding="utf-8")
        self.after.write_text("{}", encoding="utf-8")
        self.project = REPOSITORY_ROOT

    def invoke_mock(self, result=None):
        process = Mock(return_value=result or SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"ok": True, "comparison": _empty_diff()}),
            stderr="",
        ))
        output = compare_migration_graphs(
            str(self.baseline), str(self.after), bridge_path=BRIDGE,
            project_root=self.project, node_executable=sys.executable,
            run_process=process,
        )
        return output, process

    def compare_real(self, baseline: Path, after: Path):
        return compare_migration_graphs(str(baseline), str(after))

    def test_mocked_success_preserves_diff_schema_and_uses_argument_vector(self) -> None:
        expected = _empty_diff()
        output, process = self.invoke_mock(SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"ok": True, "comparison": expected}),
            stderr="",
        ))
        self.assertTrue(output["success"])
        self.assertEqual(output["comparison"], expected)
        self.assertEqual(output["behavioral_equivalence"], "not_assessed")
        args, kwargs = process.call_args
        self.assertEqual(args[0], [str(Path(sys.executable).resolve()), str(BRIDGE.resolve())])
        self.assertEqual(kwargs["cwd"], str(self.project.resolve()))
        payload = json.loads(kwargs["input"])
        self.assertEqual(payload["baselinePath"], str(self.baseline.resolve()))
        self.assertEqual(payload["afterPath"], str(self.after.resolve()))
        self.assertEqual(kwargs["timeout"], 30)

    def test_missing_baseline_and_after_paths_are_rejected_without_subprocess(self) -> None:
        process = Mock()
        missing_baseline = compare_migration_graphs(str(self.root / "missing.json"), str(self.after), run_process=process)
        self.assertFalse(missing_baseline["success"])
        self.assertEqual(missing_baseline["error"]["code"], "GRAPH_NOT_FOUND")
        self.assertEqual(missing_baseline["error"]["side"], "baseline")
        missing_after = compare_migration_graphs(str(self.baseline), str(self.root / "missing-after.json"), run_process=process)
        self.assertEqual(missing_after["error"]["side"], "after")
        process.assert_not_called()

    def test_nonzero_subprocess_exit_and_timeout_are_reported(self) -> None:
        failure, _ = self.invoke_mock(SimpleNamespace(returncode=5, stdout="", stderr="bridge failed"))
        self.assertFalse(failure["success"])
        self.assertEqual(failure["error"]["code"], "NODE_FAILED")
        timeout_process = Mock(side_effect=subprocess.TimeoutExpired(["node", "bridge"], 12))
        timeout = compare_migration_graphs(
            str(self.baseline), str(self.after), bridge_path=BRIDGE,
            project_root=self.project, node_executable=sys.executable,
            timeout_seconds=12, run_process=timeout_process,
        )
        self.assertEqual(timeout["error"]["code"], "TIMEOUT")

    def test_malformed_bridge_output_is_rejected(self) -> None:
        output, _ = self.invoke_mock(SimpleNamespace(returncode=0, stdout="not-json", stderr=""))
        self.assertEqual(output["error"]["code"], "MALFORMED_BRIDGE_OUTPUT")
        output, _ = self.invoke_mock(SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"ok": True, "comparison": {"schema_version": 1}}),
            stderr="",
        ))
        self.assertEqual(output["error"]["code"], "MALFORMED_BRIDGE_OUTPUT")

    def test_identical_graphs_return_empty_structural_diff_and_do_not_mutate_input(self) -> None:
        before = BASELINE_FIXTURE.read_bytes()
        result = self.compare_real(BASELINE_FIXTURE, BASELINE_FIXTURE)
        self.assertTrue(result["success"], result)
        comparison = result["comparison"]
        self.assertEqual(comparison["summary"]["complete"], True)
        self.assertEqual(comparison["nodes"]["added"], [])
        self.assertEqual(comparison["nodes"]["removed"], [])
        self.assertEqual(comparison["nodes"]["changed"], [])
        self.assertEqual(comparison["edges"]["added"], [])
        self.assertEqual(comparison["edges"]["removed"], [])
        self.assertEqual(comparison["edges"]["changed"], [])
        self.assertEqual(BASELINE_FIXTURE.read_bytes(), before)

    def test_fixture_comparison_exposes_additions_removals_and_metadata_changes(self) -> None:
        before = BASELINE_FIXTURE.read_bytes()
        after = AFTER_FIXTURE.read_bytes()
        result = self.compare_real(BASELINE_FIXTURE, AFTER_FIXTURE)
        self.assertTrue(result["success"], result)
        comparison = result["comparison"]
        self.assertEqual([item["node"]["id"] for item in comparison["nodes"]["added"]], ["new_service"])
        self.assertEqual([item["node"]["id"] for item in comparison["nodes"]["removed"]], ["obsolete"])
        identity_change = next(item for item in comparison["nodes"]["changed"] if "identity_changed" in item)
        self.assertTrue(any(change["field"] == "source_location" for change in identity_change["metadata_changes"]))
        self.assertTrue(comparison["edges"]["added"])
        self.assertTrue(comparison["edges"]["removed"])
        self.assertTrue(comparison["edges"]["changed"])
        self.assertEqual(BASELINE_FIXTURE.read_bytes(), before)
        self.assertEqual(AFTER_FIXTURE.read_bytes(), after)

    def test_reversed_edges_and_relationship_types_remain_distinct(self) -> None:
        baseline = self.root / "directed-baseline.json"
        after = self.root / "directed-after.json"
        graph_base = {
            "directed": False,
            "multigraph": True,
            "graph": {"schema_version": 1},
            "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
            "links": [{"source": "a", "target": "b", "relation": "calls"}],
            "hyperedges": [],
        }
        graph_after = {
            **graph_base,
            "links": [
                {"source": "b", "target": "a", "relation": "calls"},
                {"source": "a", "target": "b", "relation": "imports"},
            ],
        }
        baseline.write_text(json.dumps(graph_base), encoding="utf-8")
        after.write_text(json.dumps(graph_after), encoding="utf-8")
        result = self.compare_real(baseline, after)
        self.assertTrue(result["success"], result)
        comparison = result["comparison"]
        removed = [item["identity"] for item in comparison["edges"]["removed"]]
        added = [item["identity"] for item in comparison["edges"]["added"]]
        self.assertIn({"source": "a", "target": "b", "relation": "calls"}, removed)
        self.assertIn({"source": "b", "target": "a", "relation": "calls"}, added)
        self.assertIn({"source": "a", "target": "b", "relation": "imports"}, added)

    def test_ambiguous_label_matches_remain_unresolved_and_incomplete(self) -> None:
        baseline = self.root / "ambiguous-baseline.json"
        after = self.root / "ambiguous-after.json"
        base = {"nodes": [{"id": "old", "label": "Shared", "norm_label": "shared"}], "links": []}
        next_graph = {"nodes": [
            {"id": "new-a", "label": "Shared", "norm_label": "shared"},
            {"id": "new-b", "label": "Shared", "norm_label": "shared"},
        ], "links": []}
        baseline.write_text(json.dumps(base), encoding="utf-8")
        after.write_text(json.dumps(next_graph), encoding="utf-8")
        result = self.compare_real(baseline, after)
        self.assertTrue(result["success"], result)
        comparison = result["comparison"]
        self.assertFalse(comparison["summary"]["complete"])
        self.assertEqual(len(comparison["nodes"]["ambiguous"]), 1)
        self.assertEqual(comparison["nodes"]["added"], [])
        self.assertEqual(comparison["nodes"]["removed"], [])
        self.assertTrue(any(item["code"] == "AMBIGUOUS_NODE_MATCH" for item in comparison["warnings"]))

    def test_malformed_json_and_invalid_graph_structure_preserve_adapter_errors(self) -> None:
        malformed = self.root / "malformed.json"
        malformed.write_text("{bad", encoding="utf-8")
        result = self.compare_real(malformed, BASELINE_FIXTURE)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "GRAPH_READ_ERROR")

        invalid = self.root / "invalid.json"
        invalid.write_text(json.dumps({"nodes": []}), encoding="utf-8")
        result = self.compare_real(BASELINE_FIXTURE, invalid)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "INVALID_GRAPH")


if __name__ == "__main__":
    unittest.main()
