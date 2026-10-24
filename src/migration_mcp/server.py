"""Migration MCP server with a thin Graphify runner integration."""

import logging
import os
import sys
from typing import Any

from mcp.server import MCPServer

if __package__:
    from .analyze_legacy_repo import analyze_legacy_repo as _analyze_legacy_repo
    from .compare_migration_graphs import compare_migration_graphs as _compare_migration_graphs
    from .get_migration_context import get_migration_context as _get_migration_context
    from .remote_repository import RemoteRepositoryError, clone_public_github_repository
    from .workspaces import WorkspaceError, configured_workspace_root, resolve_workspace_path
else:  # Support the documented direct-file development command too.
    from analyze_legacy_repo import analyze_legacy_repo as _analyze_legacy_repo
    from compare_migration_graphs import compare_migration_graphs as _compare_migration_graphs
    from get_migration_context import get_migration_context as _get_migration_context
    from remote_repository import RemoteRepositoryError, clone_public_github_repository
    from workspaces import WorkspaceError, configured_workspace_root, resolve_workspace_path

server = MCPServer("migration-mcp")


@server.tool()
def health() -> str:
    """Return a simple response to confirm the server is ready."""
    return "ok"


@server.tool(structured_output=True)
def analyze_legacy_repo(
    repository_path: str | None = None,
    repository_url: str | None = None,
    revision: str | None = None,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Analyze a local repository or clone a public GitHub repository first."""
    if bool(repository_path) == bool(repository_url):
        return {
            "success": False,
            "error": {
                "code": "INVALID_REPOSITORY_INPUT",
                "message": "Provide exactly one of repository_path or repository_url.",
            },
        }
    if repository_path and revision is not None:
        return {
            "success": False,
            "error": {
                "code": "INVALID_REPOSITORY_INPUT",
                "message": "revision can only be used with repository_url.",
            },
        }
    remote: dict[str, Any] | None = None
    if repository_url:
        if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 10 <= timeout_seconds <= 600:
            return {
                "success": False,
                "error": {
                    "code": "INVALID_TIMEOUT",
                    "message": "Remote timeout_seconds must be an integer from 10 to 600.",
                },
            }
        try:
            remote = clone_public_github_repository(
                repository_url, revision, timeout_seconds=timeout_seconds
            )
            repository_path = remote["repository_path"]
        except (WorkspaceError, RemoteRepositoryError) as exc:
            return {"success": False, "error": {"code": exc.code, "message": str(exc), **exc.details}}
    else:
        try:
            if configured_workspace_root() is not None:
                repository_path = str(resolve_workspace_path(repository_path or "", field="repository_path"))
        except WorkspaceError as exc:
            return {"success": False, "error": {"code": exc.code, "message": str(exc), **exc.details}}

    result = _analyze_legacy_repo(repository_path, timeout_seconds)
    if remote is not None:
        result["remote_repository"] = {
            key: value for key, value in remote.items() if key != "warnings"
        }
        result.setdefault("warnings", []).extend(remote["warnings"])
        if remote["synthetic_package_json"]:
            result["warnings"].append(
                "A temporary package.json was added inside the analysis workspace because the existing Graphify runner requires one."
            )
    return result


@server.tool(structured_output=True)
def get_migration_context(
    graph_path: str,
    target: str,
    max_depth: int = 3,
    max_nodes: int = 60,
    max_edges: int = 60,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Retrieve bounded context for a symbol from a native Graphify graph."""
    try:
        if configured_workspace_root() is not None:
            graph_path = str(resolve_workspace_path(graph_path, field="graph_path"))
    except WorkspaceError as exc:
        return {"success": False, "graph_path": graph_path, "warnings": [], "error": {"code": exc.code, "message": str(exc), **exc.details}}
    return _get_migration_context(
        graph_path,
        target,
        max_depth,
        max_nodes,
        max_edges,
        timeout_seconds,
    )


@server.tool(structured_output=True)
def compare_migration_graphs(
    baseline_graph_path: str,
    after_graph_path: str,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Compare two Graphify snapshots using the existing JavaScript graph diff."""
    try:
        if configured_workspace_root() is not None:
            baseline_graph_path = str(resolve_workspace_path(baseline_graph_path, field="baseline_graph_path"))
            after_graph_path = str(resolve_workspace_path(after_graph_path, field="after_graph_path"))
    except WorkspaceError as exc:
        return {"success": False, "baseline_graph_path": baseline_graph_path, "after_graph_path": after_graph_path, "error": {"code": exc.code, "message": str(exc), **exc.details}}
    return _compare_migration_graphs(
        baseline_graph_path,
        after_graph_path,
        timeout_seconds,
    )


def main() -> None:
    """Run stdio locally, or authenticated Streamable HTTP when explicitly selected."""
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    transport = os.environ.get("MIGRATION_MCP_TRANSPORT", "stdio").strip().lower()
    if transport == "stdio":
        server.run(transport="stdio")
        return
    if transport != "streamable-http":
        raise RuntimeError("MIGRATION_MCP_TRANSPORT must be 'stdio' or 'streamable-http'.")

    token = os.environ.get("MIGRATION_MCP_HTTP_TOKEN", "")
    hosts = [host.strip() for host in os.environ.get(
        "MIGRATION_MCP_ALLOWED_HOSTS", os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    ).split(",") if host.strip()]
    origins = [origin.strip() for origin in os.environ.get("MIGRATION_MCP_ALLOWED_ORIGINS", "").split(",") if origin.strip()]
    try:
        port = int(os.environ.get("PORT", "8000"))
    except ValueError as exc:
        raise RuntimeError("PORT must be an integer.") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("PORT must be between 1 and 65535.")

    if __package__:
        from .http_transport import create_http_app
    else:
        from http_transport import create_http_app
    app = create_http_app(server, token, hosts, origins)
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
