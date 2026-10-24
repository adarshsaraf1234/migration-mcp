# Railway deployment

The MCP server remains stdio by default for local Codex use. Railway needs an
HTTP service, so this branch adds an opt-in Streamable HTTP mode protected by
a static bearer token. The same three MCP tools are registered in both modes.

## Deploy from the local branch

This avoids installing the Railway GitHub App. Railway documents that
`railway up` uploads the current local directory and builds it using the root
Dockerfile when present. [CLI upload and deploy](https://docs.railway.com/cli/up)

1. Install the Railway CLI (`brew install railway` on macOS) and sign in:

   ```sh
   railway login
   ```

2. From this repository checkout, create a new Railway project and deploy the
   local files from the hosted-trial branch:

   ```sh
   cd /path/to/migration-mcp-hosted-trial
   railway up --new --name migration-mcp-trial
   ```

3. In the Railway service Variables settings, set:

   - `MIGRATION_MCP_TRANSPORT=streamable-http`
   - `MIGRATION_MCP_HTTP_TOKEN` to a random secret of at least 32 characters
   - `MIGRATION_MCP_WORKSPACE_ROOT=/data/workspaces`
   - `MIGRATION_MCP_ALLOWED_HOSTS` to the exact Railway hostname (for example
     `your-service.up.railway.app`)
   - `MIGRATION_MCP_ALLOWED_ORIGINS` only if a browser-based MCP client needs
     to call the service; use that client's exact trusted origin

   Generate the secret locally with `openssl rand -hex 32` and paste it into
   Railway's secret variable field. Do not commit it. The server also discovers
   `RAILWAY_PUBLIC_DOMAIN` automatically as a default allowed host. Railway
   assigns `PORT`; the server binds to `0.0.0.0` on that port.

4. Generate a Railway public domain in the service's Networking settings.
   Keep the service restricted to your team where Railway offers that option;
   the bearer token is still required for MCP calls.

5. Check deployment logs and call `https://YOUR_DOMAIN/healthz`. Then connect
   an MCP client to `https://YOUR_DOMAIN/mcp` over Streamable HTTP and provide
   the bearer token in the `Authorization` header. First call `health`, then
   test analysis with a public GitHub repository. Repository URL restrictions
   and workspace rules are described in
   [`hosted-deployment.md`](hosted-deployment.md).

Railway supports local-directory deploys, Dockerfiles, service domains and
volumes. [Dockerfile builds](https://docs.railway.com/builds/dockerfiles) ·
[Volumes](https://docs.railway.com/volumes)

## Storage and access notes

For an initial workflow check, a persistent volume is optional: analysis,
context retrieval, and comparison can run during the same deployment. Without
a volume, generated workspaces can be lost on redeploy or container
replacement.

If attaching a volume at `/data`, note that Railway mounts volumes as root
while this Dockerfile runs as UID `10001`. Railway documents `RAILWAY_RUN_UID=0`
as the way to run a container with root-owned volumes; that makes the server
process run as root, so use it only if persistence is required for this trial.
Otherwise, adjust the image/volume ownership before enabling persistence.
[Railway volume permissions](https://docs.railway.com/volumes)

Keep the MCP port behind Railway's HTTPS domain rather than opening a raw
instance port. The server validates the HTTP `Host` header and requires a
bearer token for `/mcp`; `/healthz` is intentionally unauthenticated for service
health checks. Use a unique, high-entropy token.

## Local authenticated HTTP smoke test

You can exercise HTTP mode before deploying:

```sh
MIGRATION_MCP_TRANSPORT=streamable-http \
MIGRATION_MCP_HTTP_TOKEN="$(openssl rand -hex 32)" \
MIGRATION_MCP_ALLOWED_HOSTS=127.0.0.1:8000,localhost:8000 \
PORT=8000 \
uv run --project src/migration_mcp python src/migration_mcp/server.py
```

Then use an MCP client/Inspector with Streamable HTTP at
`http://127.0.0.1:8000/mcp` and the same token. Repository tests exercise tool
discovery, the Host allowlist, and rejection of requests without a valid
bearer token.
