# Hosted trial deployment

The `Dockerfile` packages the existing stdio server with the Node.js runner,
the verified Graphify CLI version (`graphifyy==0.9.94`), and the locked Python
MCP dependency. Glama documents that it can build a Dockerfile from a connected
GitHub repository, wrap stdio servers in its Streamable HTTP endpoint, and
mount an optional persistent volume at `/data`. Deployments are private by
default. See [Glama hosting](https://glama.ai/mcp/hosting).

## Deploy on Glama

1. Push this standalone `migration-mcp` repository or a reviewed branch to
   GitHub and connect it using the Glama GitHub App.
2. Select the repository and build from its `Dockerfile`.
3. Configure a persistent volume mounted at `/data` if workspaces need to
   survive container restarts. The process stores remote checkouts in
   `/data/workspaces` by default. Ensure the volume is writable by container
   UID `10001` (`migration`).
4. Keep the deployment private during the trial. Use its Gateway endpoint and
   access credentials in the MCP client configuration.
5. Check the Glama MCP health check/logs and verify that a tool call sees only
   the requested repository's generated artifacts.

No secrets or access tokens are required for public repositories. The initial
hosted version accepts only credential-free HTTPS URLs in this form:

```text
https://github.com/OWNER/REPOSITORY
```

Private repositories and other Git hosts are intentionally unsupported here.
Do not make this service public until an authentication and repository privacy
model has been added and reviewed.

## Hosted tool workflow

`analyze_legacy_repo` accepts one of these forms:

```json
{"repository_url":"https://github.com/OWNER/REPOSITORY","revision":"main"}
```

or, when the client can access a server-local path:

```json
{"repository_path":"/data/workspaces/WORKSPACE_ID/repository"}
```

For remote analysis, the result includes `remote_repository.workspace_id`,
`workspace_path`, `repository_path`, `repository_url`, `requested_revision`,
`resolved_commit`, and whether a synthetic `package.json` was needed. Use the
returned `artifacts.graph_json` value as `graph_path` for
`get_migration_context`. Use returned artifact paths (or copied snapshots) for
`compare_migration_graphs`.

The Graphify runner currently requires a `package.json` at the target root. If
the cloned project lacks it, the server adds a minimal marker only inside its
private analysis checkout; no change is pushed to or written back to GitHub.
This marker lets the existing runner operate and is disclosed in the result
warnings.

When `MIGRATION_MCP_WORKSPACE_ROOT` is set, local repository inputs and every
graph path passed to the other two tools must resolve within that directory.
This prevents the hosted server from reading arbitrary host paths. The Docker
image sets the root to `/data/workspaces`. Graphs and reports remain inside the
checkout, so a persistent `/data` volume retains them across restarts. Without
a persistent volume, container replacement can discard them.

The server gives each checkout a random workspace ID. Workspaces older than
the retention period become eligible for cleanup; cleanup runs before the next
remote clone. It only removes directories carrying this server's ownership
marker. Defaults are 168 hours and 25 owned workspaces. Configure:

- `MIGRATION_MCP_WORKSPACE_ROOT` — workspace root (required for remote URLs).
- `MIGRATION_MCP_WORKSPACE_TTL_HOURS` — expiry from 1 to 8760 hours.
- `MIGRATION_MCP_MAX_WORKSPACES` — maximum owned workspaces from 1 to 10000.
- `MIGRATION_MCP_GIT` — Git executable, defaults to `git`.
- `MIGRATION_MCP_NODE` — Node executable, configured by the Docker image.
- `MIGRATION_MCP_GRAPHIFY` — Graphify executable, configured by the Docker image.

## Limits and evidence

- Revisions are shallow-cloned branches or tags; each checkout is identified
  with its resolved commit hash.
- Clone timeout is bounded to 10–600 seconds. The existing Graphify runner
  timeout is also supplied through `timeout_seconds`.
- Remote clones do not recurse into submodules.
- `get_migration_context` and `compare_migration_graphs` continue to invoke the
  existing JavaScript adapters. No graph schema or native artifact is rewritten.
- Graph results are incomplete static evidence. A graph diff does not prove
  behavioral equivalence or migration success; run relevant project tests.

## Local verification

Run the fast suite:

```sh
uv run --project src/migration_mcp python -m unittest discover \
  -s src/migration_mcp/tests -v
```

The real Graphify workflow test is opt-in and uses a temporary repository:

```sh
MIGRATION_MCP_RUN_GRAPHIFY_INTEGRATION=1 \
  uv run --project src/migration_mcp python -m unittest discover \
  -s src/migration_mcp/integration_tests -v
```

Building the Docker image locally requires Docker and network access to the
base image registry and package indexes:

```sh
docker build -t migration-mcp:hosted-trial .
```
