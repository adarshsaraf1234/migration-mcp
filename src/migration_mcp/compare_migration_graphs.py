"""Python boundary for Graphify's native JavaScript graph-diff API."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable

try:
    from .analyze_legacy_repo import _resolve_executable
except ImportError:  # Support direct script imports used by the local test runner.
    from analyze_legacy_repo import _resolve_executable


_DEFAULT_TIMEOUT_SECONDS = 30
_MAX_TIMEOUT_SECONDS = 120
_OUTPUT_LIMIT = 128_000


def _failure(
    baseline_path: str,
    after_path: str,
    code: str,
    message: str,
    **details: Any,
) -> dict[str, Any]:
    return {
        "success": False,
        "baseline_graph_path": baseline_path,
        "after_graph_path": after_path,
        "error": {"code": code, "message": message, **details},
    }


def _resolve_graph_file(raw_path: str, side: str) -> tuple[Path | None, dict[str, Any] | None]:
    try:
        path = Path(raw_path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        return None, {"code": "GRAPH_NOT_FOUND", "message": f"{side} graph path could not be resolved: {exc}", "side": side}
    if not path.is_file():
        return None, {"code": "INVALID_GRAPH_PATH", "message": f"{side} graph path must refer to a file.", "side": side}
    try:
        with path.open("rb") as graph_file:
            graph_file.read(1)
    except OSError as exc:
        return None, {"code": "GRAPH_UNREADABLE", "message": f"{side} graph file is not readable: {exc}", "side": side}
    return path, None


def compare_migration_graphs(
    baseline_graph_path: str,
    after_graph_path: str,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    *,
    bridge_path: str | Path | None = None,
    project_root: str | Path | None = None,
    node_executable: str | None = None,
    path_value: str | None = None,
    run_process: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Compare two read-only Graphify snapshots with the existing JS API."""
    raw_baseline = str(baseline_graph_path) if baseline_graph_path is not None else ""
    raw_after = str(after_graph_path) if after_graph_path is not None else ""
    baseline_path, error = _resolve_graph_file(raw_baseline, "baseline")
    if error:
        return _failure(raw_baseline, raw_after, error.pop("code"), error.pop("message"), **error)
    after_path, error = _resolve_graph_file(raw_after, "after")
    if error:
        return _failure(str(baseline_path), raw_after, error.pop("code"), error.pop("message"), **error)

    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= _MAX_TIMEOUT_SECONDS:
        return _failure(
            str(baseline_path), str(after_path), "INVALID_OPTION",
            f"timeout_seconds must be an integer between 1 and {_MAX_TIMEOUT_SECONDS}.",
            option="timeout_seconds", value=timeout_seconds,
        )

    root = Path(project_root).expanduser().resolve() if project_root else Path(__file__).resolve().parents[2]
    bridge = Path(bridge_path).expanduser().resolve() if bridge_path else Path(__file__).with_name("graph_diff_bridge.mjs")
    if not bridge.is_file():
        return _failure(str(baseline_path), str(after_path), "BRIDGE_NOT_FOUND", f"Graph diff bridge was not found: {bridge}")

    search_path = path_value if path_value is not None else os.environ.get("PATH", "")
    configured_node = node_executable or os.environ.get("MIGRATION_MCP_NODE") or "node"
    node = _resolve_executable(configured_node, search_path=search_path)
    if not node:
        return _failure(
            str(baseline_path), str(after_path), "NODE_UNAVAILABLE",
            "Node.js executable was not found or is not executable. Install Node.js or set MIGRATION_MCP_NODE.",
        )

    payload = {"baselinePath": str(baseline_path), "afterPath": str(after_path)}
    try:
        completed = run_process(
            [node, str(bridge)],
            cwd=str(root),
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return _failure(
            str(baseline_path), str(after_path), "TIMEOUT",
            f"Graph comparison exceeded the {timeout_seconds}-second timeout.",
            stdout=_output_text(exc.stdout)[:_OUTPUT_LIMIT],
            stderr=_output_text(exc.stderr)[:_OUTPUT_LIMIT],
        )
    except FileNotFoundError as exc:
        return _failure(str(baseline_path), str(after_path), "NODE_UNAVAILABLE", str(exc))
    except OSError as exc:
        return _failure(str(baseline_path), str(after_path), "PROCESS_START_FAILED", str(exc))

    if not hasattr(completed, "returncode") or not isinstance(completed.returncode, int):
        return _failure(str(baseline_path), str(after_path), "MALFORMED_PROCESS_RESULT", "Node subprocess returned no valid integer exit code.")
    stdout = _output_text(getattr(completed, "stdout", ""))
    stderr = _output_text(getattr(completed, "stderr", ""))
    if completed.returncode != 0:
        return _failure(
            str(baseline_path), str(after_path), "NODE_FAILED",
            f"Graph diff bridge exited with status {completed.returncode}.",
            exit_code=completed.returncode, stderr=stderr[:_OUTPUT_LIMIT],
        )
    if len(stdout) > _OUTPUT_LIMIT:
        return _failure(
            str(baseline_path), str(after_path), "OUTPUT_TOO_LARGE",
            f"Graph diff bridge output exceeded the {_OUTPUT_LIMIT}-character response bound.",
        )
    try:
        response = json.loads(stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        return _failure(
            str(baseline_path), str(after_path), "MALFORMED_BRIDGE_OUTPUT",
            f"Graph diff bridge did not return valid JSON: {exc}", stderr=stderr[:_OUTPUT_LIMIT],
        )
    if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
        return _failure(str(baseline_path), str(after_path), "MALFORMED_BRIDGE_OUTPUT", "Graph diff bridge JSON must contain a boolean 'ok' field.")
    if response["ok"] is False:
        error = response.get("error")
        if not isinstance(error, dict) or not isinstance(error.get("code"), str):
            return _failure(str(baseline_path), str(after_path), "MALFORMED_BRIDGE_OUTPUT", "Graph diff bridge error response is missing its code.")
        return {
            "success": False,
            "baseline_graph_path": str(baseline_path),
            "after_graph_path": str(after_path),
            "error": error,
        }

    comparison = response.get("comparison")
    if not _is_diff_result(comparison):
        return _failure(
            str(baseline_path), str(after_path), "MALFORMED_BRIDGE_OUTPUT",
            "Graph diff bridge success response does not match the graph-diff result structure.",
        )
    return {
        "success": True,
        "comparison": comparison,
        "behavioral_equivalence": "not_assessed",
    }


def _is_diff_result(value: Any) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("schema_version"), int):
        return False
    if not isinstance(value.get("baseline"), dict) or not isinstance(value.get("after"), dict):
        return False
    if not isinstance(value.get("summary"), dict) or not isinstance(value.get("nodes"), dict):
        return False
    if not isinstance(value.get("edges"), dict) or not isinstance(value.get("warnings"), list):
        return False
    if not isinstance(value.get("limitations"), list):
        return False
    return all(isinstance(value["nodes"].get(name), list) for name in ("added", "removed", "changed", "ambiguous")) and all(
        isinstance(value["edges"].get(name), list) for name in ("added", "removed", "changed")
    ) and isinstance(value["summary"].get("complete"), bool)


def _output_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
