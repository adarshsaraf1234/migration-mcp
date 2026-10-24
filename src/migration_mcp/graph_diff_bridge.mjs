/** Graphify graph-diff bridge for the Python Migration MCP server. */

import { diffGraphFiles } from '../../src/graphify/graph-diff.js';

async function main() {
  let request;
  try {
    let input = '';
    for await (const chunk of process.stdin) input += chunk;
    request = JSON.parse(input);
  } catch (error) {
    process.stdout.write(JSON.stringify({
      ok: false,
      error: { code: 'INVALID_BRIDGE_INPUT', message: `Could not parse bridge input: ${error.message}` }
    }));
    return;
  }

  try {
    const comparison = await diffGraphFiles(request.baselinePath, request.afterPath);
    process.stdout.write(JSON.stringify({ ok: true, comparison }));
  } catch (error) {
    process.stdout.write(JSON.stringify({
      ok: false,
      error: {
        code: error.code ?? 'GRAPH_DIFF_ERROR',
        name: error.name ?? 'Error',
        message: error.message ?? String(error),
        details: error.details ?? {}
      }
    }));
  }
}

main().catch((error) => {
  process.stderr.write(`${error.stack ?? error}\n`);
  process.exitCode = 1;
});
