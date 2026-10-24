# Migration MCP scaffold

This isolated Python project provides the stdio MCP server for the migration
tooling. It registers `health`, `analyze_legacy_repo`, `get_migration_context`,
and `compare_migration_graphs`. Graphify extraction, graph context, and graph diff
remain implemented in JavaScript and are not duplicated here.

See [the integration guide](../../README.md) for prerequisites,
Codex MCP configuration, tool inputs and outputs, snapshot handling, and test
commands.

## Local development

From the repository root, install the isolated Python environment and start
the server with:

```sh
uv sync --project src/migration_mcp
uv run --project src/migration_mcp python src/migration_mcp/server.py
```

By default, the server communicates over stdin/stdout using MCP stdio.
Diagnostic logging goes to stderr so it does not corrupt protocol messages.
Stop it with Ctrl-C or by closing the connected MCP client. Hosted deployments
can set `MIGRATION_MCP_TRANSPORT=streamable-http`; that mode requires a
32-character-or-longer `MIGRATION_MCP_HTTP_TOKEN` and an allowed host.

## `analyze_legacy_repo`

The tool accepts exactly one of `repository_path` or `repository_url`, plus
optional `revision` and positive `timeout_seconds` (default 300). A local path
must be a directory containing `package.json` and must not be this product
repository. A remote URL is restricted to credential-free HTTPS URLs on
`github.com`; the server shallow-clones the public repo into a uniquely named
workspace. It does not support private repositories, submodules, or other Git
hosts. In hosted mode, if the cloned repository lacks `package.json`, the
server creates a temporary marker inside that clone because the existing
runner requires it; it is never written back to the remote repository.

The server launches `graphify-runner.js` with a separate argument for the
target, from the product repository as its working directory. A result has
`success`, `target_repository`, `artifacts`, `artifact_presence`, `warnings`,
and, on failure, a structured `error`.
Success requires a zero runner exit and all three native artifacts:
`graph.json`, `GRAPH_REPORT.md`, and `graph.html` under the target's
`graphify-out/` directory.

Node.js is found on the process PATH or can be set with
`MIGRATION_MCP_NODE=/absolute/path/to/node`. Graphify is searched for on PATH
and in `~/.local/bin`; set `MIGRATION_MCP_GRAPHIFY` to its executable path to
configure it explicitly. Because `graphify-runner.js` invokes the executable
as `graphify`, an explicitly configured executable must have that filename.
Remote analysis also returns `remote_repository` metadata identifying the
workspace, URL, requested revision, resolved commit and temporary marker
status. Workspaces remain available for later context and comparison calls;
the server removes only expired directories carrying its own ownership marker.
Defaults are 168 hours and 25 workspaces. Expired workspaces become eligible
for deletion and are removed before a subsequent remote clone; the server
never deletes unmarked directories.

## Hosted workspace boundary

Set `MIGRATION_MCP_WORKSPACE_ROOT` to enable remote cloning and restrict
repository and graph inputs to that directory. Without it, existing local path
behavior is retained and remote clones are rejected. The Dockerfile sets the
root to `/data/workspaces`; mount a persistent volume at `/data` if workspaces
must survive container replacement. See the root
[`hosted deployment guide`](../../docs/hosted-deployment.md).
For Railway setup, HTTP transport, authentication and testing, see
[`the Railway deployment guide`](../../docs/railway-deployment.md).

## `get_migration_context`

This tool takes a `graph_path` to an existing native Graphify JSON file and a
`target` resolvable by the existing JavaScript adapter (node id, label, or
`norm_label`). It invokes `src/graphify/graph-context.js` through the local
ESM bridge and returns its target, dependencies, callers, direct dependents,
import dependents, bounded impact, evidence, and warnings. Adapter errors such
as `NODE_NOT_FOUND`, `AMBIGUOUS_TARGET`, `INVALID_GRAPH`, and unresolved
endpoint warnings are preserved. MCP advertises the result as structured JSON.
The graph file is read only. The bridge rejects responses larger than 64,000
characters rather than returning an unbounded context payload.

Optional limits map directly to adapter options: `max_depth` (default 3,
maximum 8), `max_nodes` (default 60, maximum 200), and `max_edges` (default 60,
maximum 200). `timeout_seconds` defaults to 30 and has a maximum of 120.
Node.js is resolved the same way as for `analyze_legacy_repo`, including the
`MIGRATION_MCP_NODE` override.

The result marks evidence as bounded and incomplete. An empty result means no
matching supported edge was found in this graph; it does not establish that
the source code has no such dependency. Graphify may omit dependencies, and
the adapter traverses only `calls`, `imports`, and `imports_from` (not
hyperedges or unverified relation types).

## `compare_migration_graphs`

This tool takes existing `baseline_graph_path` and `after_graph_path` files,
checks that both are readable, and invokes the existing `diffGraphFiles`
JavaScript API through its ESM bridge. The complete versioned diff is returned
under `comparison`, including summary counts, structural additions/removals,
metadata changes, ambiguous matches, warnings, and the diff implementation's
limitations. Its identity fallback remains the implementation's: unique IDs
first, then unique `norm_label` or `label`; ambiguous candidates are reported,
not guessed. The tool preserves the diff's `summary.complete` value and marks
`behavioral_equivalence` as `not_assessed`. A successful structural comparison
does not prove behavioral correctness or migration success. Neither input
snapshot is modified. `timeout_seconds` defaults to 30 and is capped at 120.

## Tests

Fast wrapper and protocol tests use mocked subprocesses or the checked-in graph
fixtures. They do not invoke Graphify. The JavaScript adapter bridge tests need
Node.js, which is also used by the tools themselves.

```sh
uv run --project src/migration_mcp python -m unittest discover \
  -s src/migration_mcp/tests -v
```

The real workflow test is in `src/migration_mcp/integration_tests/` and is
excluded from that fast test command. It starts the server through the MCP SDK
stdio client, creates and cleans up a temporary repository, runs the existing
Graphify runner, fetches context, saves a baseline graph, adds a caller, reruns
analysis, and checks the structural graph diff. It does not use network access
or touch the product repository. Run it explicitly with:

```sh
MIGRATION_MCP_RUN_GRAPHIFY_INTEGRATION=1 \
  uv run --project src/migration_mcp python -m unittest discover \
  -s src/migration_mcp/integration_tests -v
```

The integration test reports a clear skip reason if Node.js or Graphify is
unavailable. Configure nonstandard executable locations with
`MIGRATION_MCP_NODE` and `MIGRATION_MCP_GRAPHIFY`. A successful MCP handshake
and structured tool responses also exercise the stdio protocol boundary, so
diagnostics on stderr cannot corrupt protocol messages on stdout.
