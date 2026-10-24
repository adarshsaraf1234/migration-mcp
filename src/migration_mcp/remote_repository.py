"""Clone public GitHub repositories into isolated Migration MCP workspaces."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

try:
    from .workspaces import (
        cleanup_expired_workspaces, configured_workspace_root,
        create_workspace_directory, workspace_capacity,
    )
except ImportError:  # Direct-file server invocation used in local development.
    from workspaces import (
        cleanup_expired_workspaces, configured_workspace_root,
        create_workspace_directory, workspace_capacity,
    )


_GITHUB_REPOSITORY = re.compile(r"^/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?/?$")
_REVISION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")


class RemoteRepositoryError(Exception):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def validate_github_url(repository_url: str) -> str:
    """Accept only credential-free HTTPS URLs naming one public GitHub repo."""
    if not isinstance(repository_url, str):
        raise RemoteRepositoryError("INVALID_REPOSITORY_URL", "repository_url must be a string.")
    try:
        parsed = urlsplit(repository_url.strip())
        port = parsed.port
    except ValueError as exc:
        raise RemoteRepositoryError("INVALID_REPOSITORY_URL", "repository_url is malformed.") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or not _GITHUB_REPOSITORY.fullmatch(parsed.path)
    ):
        raise RemoteRepositoryError(
            "INVALID_REPOSITORY_URL",
            "Only credential-free HTTPS URLs for a public github.com/owner/repository are supported.",
        )
    path = parsed.path.rstrip("/")
    if not path.endswith(".git"):
        path += ".git"
    return f"https://github.com{path}"


def _validate_revision(revision: str | None) -> str | None:
    if revision is None:
        return None
    if not isinstance(revision, str) or not _REVISION.fullmatch(revision) or ".." in revision:
        raise RemoteRepositoryError(
            "INVALID_REVISION",
            "revision must be a branch or tag name containing only letters, digits, '.', '_', '/', or '-'.",
        )
    return revision


def clone_public_github_repository(
    repository_url: str,
    revision: str | None = None,
    timeout_seconds: int = 180,
    *,
    workspace_root: str | Path | None = None,
    run_process: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Clone and identify a public repository without invoking a shell."""
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 10 <= timeout_seconds <= 600:
        raise RemoteRepositoryError("INVALID_TIMEOUT", "timeout_seconds must be an integer from 10 to 600.")
    normalized_url = validate_github_url(repository_url)
    revision = _validate_revision(revision)
    root = configured_workspace_root(workspace_root, required=True)
    assert root is not None

    cleanup_expired_workspaces(root)
    count, maximum = workspace_capacity(root)
    if count >= maximum:
        raise RemoteRepositoryError(
            "WORKSPACE_LIMIT_REACHED",
            "The hosted workspace limit has been reached. Remove old owned workspaces or adjust MIGRATION_MCP_MAX_WORKSPACES.",
            count=count,
            maximum=maximum,
        )

    workspace_id = str(uuid.uuid4())
    workspace = create_workspace_directory(root, workspace_id, normalized_url, revision)
    repository_path = workspace / "repository"
    command = [
        os.environ.get("MIGRATION_MCP_GIT", "git"),
        "-c", "protocol.file.allow=never",
        "clone", "--depth", "1", "--single-branch", "--no-tags", "--no-recurse-submodules",
    ]
    if revision:
        command.extend(["--branch", revision])
    command.extend(["--", normalized_url, str(repository_path)])
    git_env = os.environ.copy()
    git_env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ALLOW_PROTOCOL": "https",
        }
    )
    try:
        result = run_process(
            command,
            cwd=str(workspace),
            env=git_env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        if not hasattr(result, "returncode") or not isinstance(result.returncode, int):
            raise RemoteRepositoryError("MALFORMED_GIT_RESULT", "Git clone returned no valid exit code.")
        if result.returncode:
            raise RemoteRepositoryError(
                "CLONE_FAILED",
                f"Git clone exited with status {result.returncode}.",
                exit_code=result.returncode,
                stderr=str(getattr(result, "stderr", ""))[-2000:],
            )
        if not repository_path.is_dir():
            raise RemoteRepositoryError("CLONE_INCOMPLETE", "Git reported success but the checkout directory is missing.")

        commit_result = run_process(
            [command[0], "-C", str(repository_path), "rev-parse", "HEAD"],
            cwd=str(repository_path),
            env=git_env,
            capture_output=True,
            text=True,
            timeout=min(timeout_seconds, 30),
            check=False,
        )
        if getattr(commit_result, "returncode", 1) != 0:
            raise RemoteRepositoryError("COMMIT_LOOKUP_FAILED", "Could not identify the checked out commit.")
        commit = str(getattr(commit_result, "stdout", "")).strip()
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", commit):
            raise RemoteRepositoryError("COMMIT_LOOKUP_FAILED", "Git returned an invalid commit identifier.")

        synthetic_package_json = False
        if not (repository_path / "package.json").is_file():
            # graphify-runner.js requires this marker at the target root. It lives in the
            # private workspace checkout only and is never written back to GitHub.
            (repository_path / "package.json").write_text(
                '{"name":"migration-mcp-analysis-target","private":true}\n', encoding="utf-8"
            )
            synthetic_package_json = True

        return {
            "workspace_id": workspace_id,
            "workspace_path": str(workspace),
            "repository_path": str(repository_path.resolve()),
            "repository_url": normalized_url,
            "requested_revision": revision,
            "resolved_commit": commit,
            "synthetic_package_json": synthetic_package_json,
            "warnings": [
                "Remote repository support is limited to public GitHub repositories.",
                "The checkout is retained in the configured workspace until its retention period expires.",
            ],
        }
    except subprocess.TimeoutExpired as exc:
        shutil.rmtree(workspace, ignore_errors=True)
        raise RemoteRepositoryError("CLONE_TIMEOUT", f"Repository clone exceeded the {timeout_seconds}-second timeout.") from exc
    except FileNotFoundError as exc:
        shutil.rmtree(workspace, ignore_errors=True)
        raise RemoteRepositoryError("GIT_UNAVAILABLE", "Git executable was not found. Install Git or set MIGRATION_MCP_GIT.") from exc
    except OSError as exc:
        shutil.rmtree(workspace, ignore_errors=True)
        raise RemoteRepositoryError("CLONE_START_FAILED", f"Could not start Git: {exc}") from exc
    except RemoteRepositoryError:
        shutil.rmtree(workspace, ignore_errors=True)
        raise


__all__ = ["RemoteRepositoryError", "clone_public_github_repository", "validate_github_url"]
