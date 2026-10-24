"""Python boundary for the existing JavaScript Graphify context adapter."""

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


_DEFAULT_DEPTH = 3
_DEFAULT_NODES = 60
_DEFAULT_EDGES = 60
_DEFAULT_TIMEOUT = 30
_MAX_DEPTH = 8
_MAX_NODES = 200
_MAX_EDGES = 200
_MAX_TIMEOUT = 120
_OUTPUT_LIMIT = 64_000


def _failure(graph_path: str, code: str, message: str, **details: Any) -> dict[str, Any]:
    return {
        "success": False,
        "graph_path": graph_path,
        "warnings": [],
        "error": {"code": code, "message": message, **details},
    }


def _validate_limit(value: int, name: str, minimum: int, maximum: int) -> str | None:
    if not isinstance(value, int) or isinstance(value, bool):
        return f"{name} must be an integer between {minimum} and {maximum}."
    if value < minimum or value > maximum:
        return f"{name} must be between {minimum} and {maximum}."
    return None


def get_migration_context(
    graph_path: str,
    target: str,
    max_depth: int = _DEFAULT_DEPTH,
    max_nodes: int = _DEFAULT_NODES,
    max_edges: int = _DEFAULT_EDGES,
    timeout_seconds: int = _DEFAULT_TIMEOUT,
    *,
    bridge_path: str | Path | None = None,
    project_root: str | Path | None = None,
    node_executable: str | None = None,
    path_value: str | None = None,
    run_process: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Return bounded context from Graphify's native graph using graph-context.js."""
    raw_graph_path = str(graph_path) if graph_path is not None else ""
    try:
        resolved_graph = Path(raw_graph_path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        return _failure(raw_graph_path, "GRAPH_NOT_FOUND", f"Graph path could not be resolved: {exc}")
    resolved_graph_path = str(resolved_graph)
    if not resolved_graph.is_file():
        return _failure(resolved_graph_path, "INVALID_GRAPH_PATH", "Graph path must refer to a file.")
    if not isinstance(target, str) or not target.strip():
        return _failure(resolved_graph_path, "INVALID_TARGET", "target must be a non-empty string.")

    limits = (
        (max_depth, "max_depth", 0, _MAX_DEPTH),
        (max_nodes, "max_nodes", 0, _MAX_NODES),
        (max_edges, "max_edges", 0, _MAX_EDGES),
        (timeout_seconds, "timeout_seconds", 1, _MAX_TIMEOUT),
    )
    for value, name, minimum, maximum in limits:
        problem = _validate_limit(value, name, minimum, maximum)
        if problem:
            return _failure(resolved_graph_path, "INVALID_OPTION", problem, option=name, value=value)

    root = Path(project_root).expanduser().resolve() if project_root else Path(__file__).resolve().parents[2]
    bridge = Path(bridge_path).expanduser().resolve() if bridge_path else Path(__file__).with_name("graph_context_bridge.mjs")
    if not bridge.is_file():
        return _failure(resolved_graph_path, "BRIDGE_NOT_FOUND", f"Graph context bridge was not found: {bridge}")

    search_path = path_value if path_value is not None else os.environ.get("PATH", "")
    configured_node = node_executable or os.environ.get("MIGRATION_MCP_NODE") or "node"
    node = _resolve_executable(configured_node, search_path=search_path)
    if not node:
        return _failure(
            resolved_graph_path,
            "NODE_UNAVAILABLE",
            "Node.js executable was not found or is not executable. Install Node.js or set MIGRATION_MCP_NODE.",
        )

    payload = {
        "graphPath": resolved_graph_path,
        "target": target,
        "options": {"maxDepth": max_depth, "maxNodes": max_nodes, "maxEdges": max_edges},
    }
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
            resolved_graph_path,
            "TIMEOUT",
            f"Graph context retrieval exceeded the {timeout_seconds}-second timeout.",
            stdout=_output_text(exc.stdout)[:_OUTPUT_LIMIT],
            stderr=_output_text(exc.stderr)[:_OUTPUT_LIMIT],
        )
    except FileNotFoundError as exc:
        return _failure(resolved_graph_path, "NODE_UNAVAILABLE", str(exc))
    except OSError as exc:
        return _failure(resolved_graph_path, "PROCESS_START_FAILED", str(exc))

    if not hasattr(completed, "returncode") or not isinstance(completed.returncode, int):
        return _failure(resolved_graph_path, "MALFORMED_PROCESS_RESULT", "Node subprocess returned no valid integer exit code.")

    stdout = _output_text(getattr(completed, "stdout", ""))
    stderr = _output_text(getattr(completed, "stderr", ""))
    if completed.returncode != 0:
        return _failure(
            resolved_graph_path,
            "NODE_FAILED",
            f"Graph context bridge exited with status {completed.returncode}.",
            exit_code=completed.returncode,
            stderr=stderr[:_OUTPUT_LIMIT],
        )
    if len(stdout) > _OUTPUT_LIMIT:
        return _failure(
            resolved_graph_path,
            "OUTPUT_TOO_LARGE",
            f"Graph context bridge output exceeded the {_OUTPUT_LIMIT}-character response bound.",
        )
    try:
        response = json.loads(stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        return _failure(
            resolved_graph_path,
            "MALFORMED_BRIDGE_OUTPUT",
            f"Graph context bridge did not return valid JSON: {exc}",
            stderr=stderr[:_OUTPUT_LIMIT],
        )
    if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
        return _failure(
            resolved_graph_path,
            "MALFORMED_BRIDGE_OUTPUT",
            "Graph context bridge JSON must contain a boolean 'ok' field.",
        )
    if response["ok"] is False:
        error = response.get("error")
        if not isinstance(error, dict) or not isinstance(error.get("code"), str):
            return _failure(
                resolved_graph_path,
                "MALFORMED_BRIDGE_OUTPUT",
                "Graph context bridge error response is missing its code.",
            )
        return {
            "success": False,
            "graph_path": resolved_graph_path,
            "warnings": [],
            "error": error,
        }

    context = response.get("context")
    if not isinstance(context, dict) or not isinstance(context.get("target"), dict):
        return _failure(
            resolved_graph_path,
            "MALFORMED_BRIDGE_OUTPUT",
            "Graph context bridge success response is missing the resolved target context.",
        )

    required_shapes = {
        "dependencies": list,
        "direct_dependents": list,
        "callers": list,
        "impact": dict,
        "evidence": dict,
        "warnings": list,
    }
    malformed_fields = [
        name for name, expected_type in required_shapes.items()
        if not isinstance(context.get(name), expected_type)
    ]
    if malformed_fields:
        return _failure(
            resolved_graph_path,
            "MALFORMED_BRIDGE_OUTPUT",
            "Graph context bridge returned malformed adapter context fields.",
            fields=malformed_fields,
        )

    direct_dependents = context.get("direct_dependents", [])
    import_dependents = [
        item for item in direct_dependents
        if isinstance(item, dict)
        and isinstance(item.get("edge"), dict)
        and item["edge"].get("relation") in {"imports", "imports_from"}
    ]
    warnings = context["warnings"]
    limitations = [
        "Graphify is an extracted graph; it may omit dependencies present in the source code.",
        "An empty relationship list means no supported matching edge was found in this graph, not that no dependency exists.",
        "Impact traversal is bounded and only follows calls, imports, and imports_from; hyperedges and other relation types are not traversed.",
    ]
    evidence_completeness = {
        "status": "bounded_graph_evidence_only",
        "complete": False,
        "limitations": limitations,
    }
    result = {
        "success": True,
        "graph_path": resolved_graph_path,
        "target": context["target"],
        "matched_nodes": [context["target"]],
        "dependencies": context.get("dependencies", []),
        "callers": context.get("callers", []),
        "direct_dependents": direct_dependents,
        "import_dependents": import_dependents,
        "impact": context.get("impact", {}),
        "relationship_direction": {
            "edge_fields": "source to target",
            "calls": "source is caller; target is callee",
            "impact": "incoming supported relationships are traversed from target toward dependents",
        },
        "evidence": context.get("evidence", {}),
        "warnings": warnings,
        "evidence_completeness": evidence_completeness,
    }
    if stderr.strip():
        result["warnings"] = [*warnings, f"Node bridge wrote to stderr: {stderr[:_OUTPUT_LIMIT]}"]
    return result


def _output_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
