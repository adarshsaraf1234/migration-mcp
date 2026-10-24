/** Graphify graph-context adapter bridge for the Python MCP server. */

import { loadGraph } from '../../src/graphify/graph-context.js';

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
    const graph = await loadGraph(request.graphPath);
    const context = graph.getMigrationContext(request.target, request.options);
    process.stdout.write(JSON.stringify({ ok: true, context }));
  } catch (error) {
    process.stdout.write(JSON.stringify({
      ok: false,
      error: {
        code: error.code ?? 'ADAPTER_ERROR',
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
