"""Constrained storage and lifecycle helpers for hosted MCP workspaces."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any


_OWNER_MARKER = ".migration-mcp-workspace.json"
_OWNER_VALUE = "migration-mcp-public-github-workspace-v1"
_DEFAULT_TTL_HOURS = 168
_DEFAULT_MAX_WORKSPACES = 25


class WorkspaceError(Exception):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def configured_workspace_root(value: str | Path | None = None, *, required: bool = False) -> Path | None:
    raw = value if value is not None else os.environ.get("MIGRATION_MCP_WORKSPACE_ROOT")
    if not raw:
        if required:
            raise WorkspaceError(
                "WORKSPACE_ROOT_NOT_CONFIGURED",
                "Set MIGRATION_MCP_WORKSPACE_ROOT before using remote repositories or hosted path restrictions.",
            )
        return None
    root = Path(raw).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise WorkspaceError("INVALID_WORKSPACE_ROOT", "Configured workspace root is not a directory.")
    return root


def resolve_workspace_path(
    raw_path: str | Path,
    *,
    workspace_root: str | Path | None = None,
    must_exist: bool = True,
    field: str = "path",
) -> Path:
    raw = str(raw_path) if raw_path is not None else ""
    if not raw.strip():
        raise WorkspaceError("INVALID_PATH", f"{field} must be a non-empty path.", field=field)
    try:
        path = Path(raw).expanduser().resolve(strict=must_exist)
    except (OSError, RuntimeError) as exc:
        raise WorkspaceError("PATH_NOT_FOUND", f"{field} could not be resolved: {exc}", field=field) from exc

    root = configured_workspace_root(workspace_root)
    if root is not None:
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise WorkspaceError(
                "PATH_OUTSIDE_WORKSPACE",
                f"{field} must be inside the configured Migration MCP workspace.",
                field=field,
                workspace_root=str(root),
            ) from exc
    return path


def cleanup_expired_workspaces(root: Path, *, now: float | None = None) -> int:
    """Remove only expired directories carrying this adapter's ownership marker."""
    ttl_hours = _bounded_env_int("MIGRATION_MCP_WORKSPACE_TTL_HOURS", _DEFAULT_TTL_HOURS, 1, 24 * 365)
    cutoff = (time.time() if now is None else now) - ttl_hours * 60 * 60
    removed = 0
    for candidate in root.iterdir():
        if candidate.is_symlink() or not candidate.is_dir() or not candidate.name.startswith("workspace-"):
            continue
        marker = candidate / _OWNER_MARKER
        try:
            if marker.is_symlink() or not marker.is_file():
                continue
            metadata = json.loads(marker.read_text(encoding="utf-8"))
            created_at = metadata.get("created_at")
            if metadata.get("owner") != _OWNER_VALUE or not isinstance(created_at, (int, float)):
                continue
            if created_at < cutoff:
                shutil.rmtree(candidate)
                removed += 1
        except (OSError, ValueError, TypeError):
            continue
    return removed


def workspace_capacity(root: Path) -> tuple[int, int]:
    maximum = _bounded_env_int("MIGRATION_MCP_MAX_WORKSPACES", _DEFAULT_MAX_WORKSPACES, 1, 10_000)
    owned = 0
    for candidate in root.iterdir():
        if candidate.is_symlink() or not candidate.is_dir() or not candidate.name.startswith("workspace-"):
            continue
        marker = candidate / _OWNER_MARKER
        try:
            if not marker.is_symlink() and json.loads(marker.read_text(encoding="utf-8")).get("owner") == _OWNER_VALUE:
                owned += 1
        except (OSError, ValueError, TypeError):
            continue
    return owned, maximum


def create_workspace_directory(root: Path, workspace_id: str, repository_url: str, revision: str | None) -> Path:
    directory = root / f"workspace-{workspace_id}"
    directory.mkdir(mode=0o700)
    marker = directory / _OWNER_MARKER
    marker.write_text(
        json.dumps(
            {
                "owner": _OWNER_VALUE,
                "workspace_id": workspace_id,
                "repository_url": repository_url,
                "revision": revision,
                "created_at": time.time(),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return directory


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise WorkspaceError("INVALID_WORKSPACE_SETTING", f"{name} must be an integer.", setting=name) from exc
    if not minimum <= value <= maximum:
        raise WorkspaceError(
            "INVALID_WORKSPACE_SETTING",
            f"{name} must be between {minimum} and {maximum}.",
            setting=name,
            value=value,
        )
    return value


__all__ = [
    "WorkspaceError",
    "cleanup_expired_workspaces",
    "configured_workspace_root",
    "create_workspace_directory",
    "resolve_workspace_path",
    "workspace_capacity",
]
