# Graphify integration contract (verified)

This contract records behavior verified with the Graphify CLI `0.9.94` on 2026-10-23. `graphify --version` reported `graphify 0.9.94`. `graphify --help` is the supported full CLI help entry point. Subcommand `--help` is not consistently implemented: for example `graphify extract --help` prints the global usage hint rather than extract-specific help. Executable locations are installation-specific; use PATH or configure the local executable path rather than relying on a particular machine's install directory.

## Targeted analysis and artifact paths

Run from `graph-migrate-codex`:

```sh
graphify extract ../legacy-demo-app --code-only --no-cluster
graphify cluster-only ../legacy-demo-app --no-label
```

The extraction log explicitly said it was scanning the designated sibling `legacy-demo-app` fixture repository, found 7 code files, and wrote 21 nodes and 34 raw edges. The product repository was not scanned. `--code-only` avoids semantic LLM extraction; `--no-cluster` leaves the raw extraction for the following clustering/report pass. `cluster-only --no-label` produced:

- `../legacy-demo-app/graphify-out/graph.json`
- `../legacy-demo-app/graphify-out/GRAPH_REPORT.md`
- `../legacy-demo-app/graphify-out/graph.html`

Graphify also created its native cache, manifest, marker, and analysis metadata under `graphify-out/`. Keep these outputs in Graphify's own format. Running `npm run graphify:analyze` repeats the same targeted workflow and checks for the three primary artifacts. A different repository can be passed as the runner's first argument.

Observed count caveat: the generated report says `21 nodes · 33 edges`, while the final `graph.json` contained 34 `links` on the subsequent validation run. The first immediate post-clustering inspection counted 33 links. Treat Graphify's current report/JSON count mismatch as native output behavior to investigate separately; the runner preserves both artifacts and does not rewrite either.

## Observed JSON shape

The final graph after `cluster-only` is a NetworkX-style JSON object with top-level `directed`, `multigraph`, `graph`, `nodes`, `links`, and `hyperedges` keys. For this run, `directed` was `false`, `multigraph` was `true`, `graph` contained `schema_version: 1` and `graphify_version: "0.9.94"`, and `hyperedges` was empty. The raw no-cluster extraction instead had `nodes`, `edges`, `hyperedges`, `input_tokens`, and `output_tokens`; Graphify converted `edges` to `links` in the clustered output and added its metadata. Consumers should preserve and accept Graphify's native output rather than assuming a custom schema.

Observed node fields included `id`, `label`, `_origin`, `file_type`, `source_file`, `source_location`, `norm_label`, `community`, and `community_name`; callable symbols also had `_callable: true`. Fields are optional by node kind. Observed link fields included `source`, `target`, `relation`, `_origin`, `confidence`, `confidence_score`, `context`, `source_file`, `source_location`, and `weight`. In this code-only run links had `_origin: "ast"`, `confidence: "EXTRACTED"`, and `confidence_score: 1`. The observed relations were `calls`, `contains`, `imports`, and `imports_from`. Graphify may add other relation kinds or fields for other extractors/modes; this spike does not assert unobserved values.

## Relationship direction and callback utility impact

Each link's `source` is the relationship origin and `target` is its destination. The observed `calls` links point from caller to callee:

- `createOrder()` (`order_service_createorder`) → `findById()` (`legacy_utils_findbyid`), recorded at `order-service.js:L7`.
- `describeProduct()` (`report_describeproduct`) → `findById()` (`legacy_utils_findbyid`), recorded at `report.js:L5`.

The same callers have `imports` links to the utility symbol from their respective source files. `imports_from` points from each source file node to the imported module node. `contains` points from a file node to a symbol it defines. Tests add a call from `callCreateOrder()` to `createOrder()`.

Although final graph metadata had `directed: false`, the CLI `path` command uses directed traversal by default and explicitly reported no directed path from `findById` back to `createOrder`. The caller-to-utility query succeeds, and `graphify path createOrder findById --graph ../legacy-demo-app/graphify-out/graph.json` returned the one-hop `calls` path. `query --seed findById` traversed the call relationships and returned both production callers and the test helper. `explain findById --all` showed five immediate connections; `affected findById` showed reverse-impact results including both callers and the tests. Use explicit graph paths in all CLI calls so analysis cannot silently target a different repository's default `graphify-out`.

Verified query examples:

```sh
graphify query 'findById callers' --seed findById --context call --graph ../legacy-demo-app/graphify-out/graph.json
graphify explain findById --all --graph ../legacy-demo-app/graphify-out/graph.json
graphify path createOrder findById --graph ../legacy-demo-app/graphify-out/graph.json
graphify affected findById --graph ../legacy-demo-app/graphify-out/graph.json
```

## Bounded migration impact candidate

The strongest direct impact set for a behavior change to `findById` is its two production callers, `createOrder()` and `describeProduct()`. A bounded experiment can change only the utility's callback contract, adapt those callers, and rerun the six legacy tests. Keep the catalog and externally observed order/report behavior as controls. This is an analysis recommendation only; no migration has been performed.
