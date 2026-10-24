"""Unit tests for the Graphify runner subprocess wrapper."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_legacy_repo import analyze_legacy_repo


class AnalyzeLegacyRepoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "product"
        self.project.mkdir()
        self.runner = self.project / "graphify-runner.js"
        self.runner.write_text("// test runner placeholder\n", encoding="utf-8")
        self.target = self.root / "legacy"
        self.target.mkdir()
        (self.target / "package.json").write_text('{"name":"legacy"}', encoding="utf-8")
        self.graphify_dir = self.root / "bin"
        self.graphify_dir.mkdir()
        self.graphify = self.graphify_dir / "graphify"
        self.graphify.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.graphify.chmod(0o755)
        self.kwargs = {
            "runner_path": self.runner,
            "project_root": self.project,
            "node_executable": sys.executable,
            "graphify_executable": str(self.graphify),
            "path_value": "",
            "home": self.root / "home",
        }

    def make_artifacts(self) -> Path:
        output = self.target / "graphify-out"
        output.mkdir(exist_ok=True)
        for name in ("graph.json", "GRAPH_REPORT.md", "graph.html"):
            (output / name).write_text("{}", encoding="utf-8")
        return output

    def run_analysis(self, process: Mock, **overrides):
        return analyze_legacy_repo(
            str(self.target), run_process=process, **(self.kwargs | overrides)
        )

    def test_valid_execution_uses_argument_vector_explicit_cwd_and_environment(self) -> None:
        self.make_artifacts()
        process = Mock(return_value=SimpleNamespace(returncode=0, stdout="done", stderr=""))

        result = self.run_analysis(process)

        self.assertTrue(result["success"])
        args, kwargs = process.call_args
        command = args[0]
        self.assertEqual(command[0], str(Path(sys.executable).resolve()))
        self.assertEqual(command[1:], [str(self.runner.resolve()), str(self.target.resolve())])
        self.assertEqual(kwargs["cwd"], str(self.project.resolve()))
        self.assertIn(str(self.graphify_dir.resolve()), kwargs["env"]["PATH"].split(":"))
        self.assertEqual(kwargs["timeout"], 300)
        self.assertFalse(kwargs.get("shell", False))

    def test_missing_repository_is_reported(self) -> None:
        result = analyze_legacy_repo(str(self.root / "absent"), **self.kwargs)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "repository_not_found")

    def test_invalid_repository_without_package_json_is_reported(self) -> None:
        invalid = self.root / "not-a-repo"
        invalid.mkdir()
        result = analyze_legacy_repo(str(invalid), **self.kwargs)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "invalid_repository")

    def test_runner_not_found_is_reported(self) -> None:
        result = analyze_legacy_repo(
            str(self.target), **(self.kwargs | {"runner_path": self.root / "missing.js"})
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "runner_not_found")

    def test_graphify_unavailable_is_reported(self) -> None:
        result = analyze_legacy_repo(
            str(self.target), **(self.kwargs | {
                "graphify_executable": "graphify",
                "path_value": "",
                "home": self.root / "empty-home",
            }),
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "graphify_unavailable")

    def test_nonzero_subprocess_exit_is_failure_even_if_old_artifacts_exist(self) -> None:
        self.make_artifacts()
        process = Mock(return_value=SimpleNamespace(returncode=1, stdout="", stderr="failed"))
        result = self.run_analysis(process)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "runner_failed")
        self.assertEqual(result["error"]["exit_code"], 1)

    def test_timeout_is_reported(self) -> None:
        process = Mock(side_effect=subprocess.TimeoutExpired(["node", "runner"], 17))
        result = self.run_analysis(process, timeout_seconds=17)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "timeout")

    def test_missing_output_artifacts_prevent_success(self) -> None:
        process = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
        result = self.run_analysis(process)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "missing_artifacts")
        self.assertCountEqual(
            result["error"]["missing_artifacts"],
            ["graph_json", "graph_report", "graph_html"],
        )

    def test_all_native_artifacts_are_reported_after_success(self) -> None:
        output = self.make_artifacts()
        process = Mock(return_value=SimpleNamespace(returncode=0, stdout="verified", stderr=""))
        result = self.run_analysis(process)
        self.assertTrue(result["success"])
        output = output.resolve()
        self.assertEqual(result["artifacts"]["graph_json"], str(output / "graph.json"))
        self.assertEqual(result["artifacts"]["graph_report"], str(output / "GRAPH_REPORT.md"))
        self.assertEqual(result["artifacts"]["graph_html"], str(output / "graph.html"))
        self.assertTrue(all(result["artifact_presence"].values()))


if __name__ == "__main__":
    unittest.main()
