# Migration MCP

A small stdio MCP server for Graphify-based legacy migration analysis. The Python server exposes `analyze_legacy_repo`, `get_migration_context`, and `compare_migration_graphs`. It reuses the bundled Graphify runner and JavaScript context/diff adapters; it does not modify native Graphify artifacts.

## Requirements

- Python 3.11+
- `uv` (or another installer for the declared `mcp>=2,<3` dependency)
- Node.js
- Graphify CLI for repository analysis

## Install and run

From the repository root:

```sh
uv sync --project src/migration_mcp
uv run --project src/migration_mcp python src/migration_mcp/server.py
```

For nonstandard executable locations, set `MIGRATION_MCP_NODE` and `MIGRATION_MCP_GRAPHIFY` in the MCP client's environment. Graphify is required for `analyze_legacy_repo`; context and comparison operate on existing native graph snapshots.

## Tools

- `analyze_legacy_repo(repository_path, timeout_seconds?)` runs the bundled Graphify runner and verifies `graph.json`, `GRAPH_REPORT.md`, and `graph.html`.
- `get_migration_context(graph_path, target, max_depth?, max_nodes?, max_edges?, timeout_seconds?)` returns bounded context from the native graph.
- `compare_migration_graphs(baseline_graph_path, after_graph_path, timeout_seconds?)` returns the structural graph diff.

See [the Graphify contract](docs/graphify-contract.md) for verified graph behavior. The server package README contains detailed schemas, error behavior, and test commands.

## Tests

```sh
uv run --project src/migration_mcp python -m unittest discover -s src/migration_mcp/tests -v
```

The opt-in real Graphify workflow test is documented in `src/migration_mcp/README.md`. Structural graph differences do not establish behavioral equivalence; run and review relevant tests separately.
