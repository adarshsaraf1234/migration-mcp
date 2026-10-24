"""Unit tests for safe public GitHub checkout handling."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from remote_repository import (
    RemoteRepositoryError,
    clone_public_github_repository,
    validate_github_url,
)
from workspaces import WorkspaceError, cleanup_expired_workspaces, resolve_workspace_path
import server


class RemoteRepositoryTest(unittest.TestCase):
    def test_normalizes_supported_public_github_url(self):
        self.assertEqual(
            validate_github_url("https://github.com/example/demo"),
            "https://github.com/example/demo.git",
        )

    def test_rejects_non_github_and_credential_urls(self):
        for url in (
            "https://gitlab.com/example/demo",
            "https://user:secret@github.com/example/demo",
            "https://github.com/example/demo/tree/main",
            "file:///tmp/repo",
        ):
            with self.subTest(url=url), self.assertRaises(RemoteRepositoryError):
                validate_github_url(url)

    def test_clone_uses_argument_vector_and_creates_workspace_marker_and_package_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            def fake_process(command, **kwargs):
                if command[1:3] == ["-c", "protocol.file.allow=never"]:
                    repo_dir = Path(command[-1])
                    repo_dir.mkdir()
                    return SimpleNamespace(returncode=0, stdout="cloned", stderr="")
                return SimpleNamespace(returncode=0, stdout="a" * 40, stderr="")

            process = Mock(side_effect=fake_process)
            result = clone_public_github_repository(
                "https://github.com/example/demo",
                "main",
                workspace_root=root,
                run_process=process,
            )
            self.assertEqual(result["repository_url"], "https://github.com/example/demo.git")
            self.assertEqual(result["requested_revision"], "main")
            self.assertEqual(result["resolved_commit"], "a" * 40)
            self.assertTrue(result["synthetic_package_json"])
            self.assertTrue((Path(result["repository_path"]) / "package.json").is_file())
            self.assertTrue((Path(result["workspace_path"]) / ".migration-mcp-workspace.json").is_file())
            self.assertEqual(process.call_count, 2)
            self.assertTrue(all(not call.kwargs.get("shell", False) for call in process.call_args_list))

    def test_clone_timeout_cleans_only_created_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            unrelated = root / "keep-me"
            unrelated.mkdir()
            process = Mock(side_effect=subprocess.TimeoutExpired(["git"], 10))
            with self.assertRaises(RemoteRepositoryError) as caught:
                clone_public_github_repository(
                    "https://github.com/example/demo",
                    workspace_root=root,
                    run_process=process,
                )
            self.assertEqual(caught.exception.code, "CLONE_TIMEOUT")
            self.assertEqual(list(root.iterdir()), [unrelated])

    def test_expiry_cleanup_removes_only_owned_expired_workspaces(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            expired = root / "workspace-expired"
            expired.mkdir()
            (expired / ".migration-mcp-workspace.json").write_text(
                json.dumps({"owner": "migration-mcp-public-github-workspace-v1", "created_at": 1}),
                encoding="utf-8",
            )
            unrelated = root / "workspace-unrelated"
            unrelated.mkdir()
            (unrelated / ".migration-mcp-workspace.json").write_text(
                json.dumps({"owner": "some-other-service", "created_at": 1}),
                encoding="utf-8",
            )
            self.assertEqual(cleanup_expired_workspaces(root, now=1_000_000), 1)
            self.assertFalse(expired.exists())
            self.assertTrue(unrelated.exists())

    def test_workspace_paths_are_confined_when_root_is_configured(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inside = root / "graph.json"
            inside.write_text("{}", encoding="utf-8")
            self.assertEqual(resolve_workspace_path(inside, workspace_root=root), inside.resolve())
            outside = root.parent / "outside-graph.json"
            outside.write_text("{}", encoding="utf-8")
            try:
                with self.assertRaises(WorkspaceError) as caught:
                    resolve_workspace_path(outside, workspace_root=root)
                self.assertEqual(caught.exception.code, "PATH_OUTSIDE_WORKSPACE")
            finally:
                outside.unlink()

    def test_analyze_tool_passes_remote_checkout_to_existing_graphify_runner(self):
        remote = {
            "workspace_id": "workspace-id",
            "workspace_path": "/data/workspaces/workspace-id",
            "repository_path": "/data/workspaces/workspace-id/repository",
            "repository_url": "https://github.com/example/demo.git",
            "requested_revision": "main",
            "resolved_commit": "a" * 40,
            "synthetic_package_json": True,
            "warnings": ["public repository only"],
        }
        analysis = {"success": True, "artifacts": {"graph_json": "/data/workspaces/workspace-id/repository/graphify-out/graph.json"}, "warnings": []}
        with patch("server.clone_public_github_repository", return_value=remote), patch(
            "server._analyze_legacy_repo", return_value=analysis
        ) as analyze:
            result = server.analyze_legacy_repo(repository_url="https://github.com/example/demo", revision="main")
        analyze.assert_called_once_with(remote["repository_path"], 300)
        self.assertTrue(result["success"])
        self.assertEqual(result["remote_repository"]["resolved_commit"], "a" * 40)
        self.assertEqual(len(result["warnings"]), 2)


if __name__ == "__main__":
    unittest.main()
