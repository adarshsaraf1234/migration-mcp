"""Migration MCP server with a thin Graphify runner integration."""

import logging
import sys
from typing import Any

from mcp.server import MCPServer

if __package__:
    from .analyze_legacy_repo import analyze_legacy_repo as _analyze_legacy_repo
    from .compare_migration_graphs import compare_migration_graphs as _compare_migration_graphs
    from .get_migration_context import get_migration_context as _get_migration_context
else:  # Support the documented direct-file development command too.
    from analyze_legacy_repo import analyze_legacy_repo as _analyze_legacy_repo
    from compare_migration_graphs import compare_migration_graphs as _compare_migration_graphs
    from get_migration_context import get_migration_context as _get_migration_context

server = MCPServer("migration-mcp")


@server.tool()
def health() -> str:
    """Return a simple response to confirm the server is ready."""
    return "ok"


@server.tool(structured_output=True)
def analyze_legacy_repo(repository_path: str, timeout_seconds: int = 300) -> dict[str, Any]:
    """Run the existing Graphify workflow for a separate legacy repository."""
    return _analyze_legacy_repo(repository_path, timeout_seconds)


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
    return _compare_migration_graphs(
        baseline_graph_path,
        after_graph_path,
        timeout_seconds,
    )


def main() -> None:
    """Run the server over MCP's stdio transport."""
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
