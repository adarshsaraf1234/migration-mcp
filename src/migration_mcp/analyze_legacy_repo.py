"""Safe subprocess integration with the repository's existing Graphify runner."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable


_ARTIFACTS = {
    "graph_json": "graph.json",
    "graph_report": "GRAPH_REPORT.md",
    "graph_html": "graph.html",
}
_DEFAULT_TIMEOUT_SECONDS = 300
_OUTPUT_LIMIT = 4_000


def _failure(
    target: str,
    artifacts: dict[str, str],
    code: str,
    message: str,
    warnings: list[str] | None = None,
    artifact_presence: dict[str, bool | None] | None = None,
    **details: Any,
) -> dict[str, Any]:
    return {
        "success": False,
        "target_repository": target,
        "artifacts": artifacts,
        "artifact_presence": (
            artifact_presence
            if artifact_presence is not None
            else {key: None for key in artifacts}
        ),
        "warnings": warnings or [],
        "error": {"code": code, "message": message, **details},
    }


def _resolve_executable(
    executable: str,
    *,
    search_path: str,
) -> str | None:
    expanded = Path(executable).expanduser()
    if expanded.is_absolute() or os.sep in executable:
        candidate = expanded.resolve()
        return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None
    return shutil.which(executable, path=search_path)


def _graphify_search_path(path_value: str | None, home: Path) -> str:
    entries = [str(home / ".local" / "bin")]
    entries.extend((path_value or "").split(os.pathsep))
    return os.pathsep.join(entry for entry in dict.fromkeys(entries) if entry)


def analyze_legacy_repo(
    repository_path: str,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    *,
    runner_path: str | Path | None = None,
    project_root: str | Path | None = None,
    node_executable: str | None = None,
    graphify_executable: str | None = None,
    path_value: str | None = None,
    home: str | Path | None = None,
    run_process: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Run Graphify for a target repository and verify its native artifacts.

    Keyword-only paths and process injection are intended for deterministic tests.
    """
    raw_target = str(repository_path) if repository_path is not None else ""
    try:
        target_path = Path(raw_target).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        return _failure(raw_target, {}, "repository_not_found", f"Repository path could not be resolved: {exc}")

    output_dir = target_path / "graphify-out"
    artifact_paths = {key: str(output_dir / name) for key, name in _ARTIFACTS.items()}

    if not target_path.is_dir():
        return _failure(str(target_path), artifact_paths, "invalid_repository", "Repository path must be a directory.")

    resolved_project_root = Path(project_root).resolve() if project_root else Path(__file__).resolve().parents[2]
    if target_path == resolved_project_root:
        return _failure(
            str(target_path), artifact_paths, "invalid_repository",
            "Refusing to analyze the Migration MCP product repository.",
        )
    if not (target_path / "package.json").is_file():
        return _failure(
            str(target_path), artifact_paths, "invalid_repository",
            "Target does not meet the Graphify runner requirement: package.json is missing.",
        )
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
        return _failure(
            str(target_path), artifact_paths, "invalid_timeout",
            "timeout_seconds must be a positive integer.",
        )

    resolved_runner = Path(runner_path).expanduser().resolve() if runner_path else resolved_project_root / "graphify-runner.js"
    if not resolved_runner.is_file():
        return _failure(
            str(target_path), artifact_paths, "runner_not_found",
            f"Graphify runner was not found: {resolved_runner}",
        )

    selected_home = Path(home).expanduser() if home else Path.home()
    base_path = path_value if path_value is not None else os.environ.get("PATH", "")
    search_path = _graphify_search_path(base_path, selected_home)

    configured_graphify = graphify_executable or os.environ.get("MIGRATION_MCP_GRAPHIFY") or "graphify"
    graphify_path = _resolve_executable(configured_graphify, search_path=search_path)
    if not graphify_path:
        return _failure(
            str(target_path), artifact_paths, "graphify_unavailable",
            "Graphify CLI 'graphify' was not found or is not executable. Install it or set "
            "MIGRATION_MCP_GRAPHIFY to its executable path.",
        )
    if Path(graphify_path).name != "graphify":
        return _failure(
            str(target_path), artifact_paths, "graphify_unavailable",
            "The configured Graphify executable must be named 'graphify' because the existing "
            "graphify-runner.js invokes that command.",
        )

    configured_node = node_executable or os.environ.get("MIGRATION_MCP_NODE") or "node"
    node_path = _resolve_executable(configured_node, search_path=base_path)
    if not node_path:
        return _failure(
            str(target_path), artifact_paths, "node_unavailable",
            "Node.js executable was not found or is not executable. Install Node.js or set "
            "MIGRATION_MCP_NODE to its executable path.",
        )

    child_env = os.environ.copy()
    child_env["PATH"] = os.pathsep.join([str(Path(graphify_path).parent), search_path])
    child_env["MIGRATION_MCP_GRAPHIFY"] = graphify_path
    command = [node_path, str(resolved_runner), str(target_path)]
    try:
        completed = run_process(
            command,
            cwd=str(resolved_project_root),
            env=child_env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return _failure(
            str(target_path), artifact_paths, "timeout",
            f"Graphify analysis exceeded the {timeout_seconds}-second timeout.",
            stdout=_output_text(exc.stdout), stderr=_output_text(exc.stderr),
        )
    except FileNotFoundError as exc:
        return _failure(str(target_path), artifact_paths, "executable_not_found", str(exc))
    except OSError as exc:
        return _failure(str(target_path), artifact_paths, "process_start_failed", str(exc))

    if not hasattr(completed, "returncode") or not isinstance(completed.returncode, int):
        return _failure(
            str(target_path), artifact_paths, "malformed_process_result",
            "Subprocess returned no valid integer exit code.",
        )
    stdout = _output_text(getattr(completed, "stdout", ""))
    stderr = _output_text(getattr(completed, "stderr", ""))
    presence = {key: Path(path).is_file() for key, path in artifact_paths.items()}
    warnings: list[str] = []
    if stderr.strip():
        warnings.append(f"Graphify runner wrote to stderr: {stderr[:_OUTPUT_LIMIT]}")

    if completed.returncode != 0:
        return _failure(
            str(target_path), artifact_paths, "runner_failed",
            f"Graphify runner exited with status {completed.returncode}.",
            warnings=warnings,
            exit_code=completed.returncode,
            stdout=stdout[:_OUTPUT_LIMIT],
            stderr=stderr[:_OUTPUT_LIMIT],
            artifact_presence=presence,
        )

    missing = [key for key, exists in presence.items() if not exists]
    if missing:
        return _failure(
            str(target_path), artifact_paths, "missing_artifacts",
            "Graphify runner exited successfully but required artifacts are missing.",
            warnings=warnings,
            missing_artifacts=missing,
            stdout=stdout[:_OUTPUT_LIMIT],
            stderr=stderr[:_OUTPUT_LIMIT],
            artifact_presence=presence,
        )

    return {
        "success": True,
        "target_repository": str(target_path),
        "artifacts": artifact_paths,
        "artifact_presence": presence,
        "warnings": warnings,
        "stdout": stdout[:_OUTPUT_LIMIT],
    }


def _output_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
