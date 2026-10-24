import { GraphContext, GraphContextError, loadGraph } from './graph-context.js';

const RESULT_SCHEMA_VERSION = 1;
const compareText = (left, right) => left < right ? -1 : left > right ? 1 : 0;

export class GraphDiffError extends Error {
  constructor(code, message, details = {}) {
    super(message);
    this.name = 'GraphDiffError';
    this.code = code;
    this.details = details;
  }
}

function stableStringify(value) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(',')}]`;
  return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`).join(',')}}`;
}

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value));
}

async function asContext(input, side) {
  if (input instanceof GraphContext) return input;
  if (typeof input === 'string') return loadGraph(input);
  if (!input || typeof input !== 'object' || !Array.isArray(input.nodes)) {
    throw new GraphDiffError('INVALID_GRAPH', `${side} graph must be a GraphContext, graph path, or object with a nodes array.`, { side });
  }
  const edgeKey = Array.isArray(input.links) ? 'links' : Array.isArray(input.edges) ? 'edges' : null;
  if (!edgeKey) {
    throw new GraphDiffError('INVALID_GRAPH', `${side} graph must contain a links or edges array.`, { side });
  }
  return new GraphContext(input, undefined, edgeKey);
}

function snapshotInfo(context) {
  return {
    path: context.graphPath ?? null,
    graphify_version: context.graph.graph?.graphify_version ?? null,
    schema_version: context.graph.graph?.schema_version ?? null,
    node_count: context.nodes.length,
    edge_count: context.edges.length,
    edge_key: context.edgeKey
  };
}

function addWarning(warnings, side, code, message, details = {}) {
  warnings.push({ side, code, message, ...details });
}

function uniqueNodesById(context, side, warnings) {
  const output = new Map();
  for (const node of context.nodes) {
    if (!node || typeof node !== 'object' || typeof node.id !== 'string' || !node.id) continue;
    const values = context.nodesById.get(node.id);
    if (values?.length === 1) output.set(node.id, node);
  }
  const duplicateIds = [...context.nodesById.entries()].filter(([, nodes]) => nodes.length > 1);
  for (const [id, nodes] of duplicateIds) {
    addWarning(warnings, side, 'DUPLICATE_NODE_ID', `Duplicate node id "${id}"; those nodes are excluded from identity matching.`, { id, count: nodes.length });
  }
  const missingIds = context.nodes.filter((node) => !node || typeof node !== 'object' || typeof node.id !== 'string' || !node.id);
  if (missingIds.length) {
    addWarning(warnings, side, 'MISSING_NODE_ID', `${missingIds.length} node(s) lack a usable id and are excluded from identity matching.`, { count: missingIds.length });
  }
  return output;
}

function addContextWarnings(context, side, warnings) {
  for (const message of context.warnings) {
    const incomplete = /unresolved or ambiguous endpoint|no usable id|Duplicate node id|missing source, target, or relation|not an object/.test(message);
    addWarning(warnings, side, incomplete ? 'INCOMPLETE_GRAPH' : 'GRAPH_WARNING', message);
  }
}

function groupBy(nodes, property) {
  const groups = new Map();
  for (const node of nodes) {
    const value = node[property];
    if (typeof value !== 'string' || !value) continue;
    if (!groups.has(value)) groups.set(value, []);
    groups.get(value).push(node);
  }
  for (const values of groups.values()) values.sort((a, b) => compareText(a.id, b.id));
  return groups;
}

function matchNodes(beforeById, afterById, warnings) {
  const beforeToCanonical = new Map();
  const afterToCanonical = new Map();
  const matchedBefore = new Set();
  const matchedAfter = new Set();
  const ambiguousBefore = new Set();
  const ambiguousAfter = new Set();
  const ambiguous = [];
  const changed = [];

  for (const id of [...beforeById.keys()].filter((value) => afterById.has(value)).sort()) {
    const before = beforeById.get(id);
    const after = afterById.get(id);
    const canonical = `node-id:${id}`;
    beforeToCanonical.set(id, canonical);
    afterToCanonical.set(id, canonical);
    matchedBefore.add(id);
    matchedAfter.add(id);
    const metadataChanges = compareMetadata(before, after, new Set(['id']));
    if (metadataChanges.length) changed.push({ identity: { type: 'id', value: id }, metadata_changes: metadataChanges, before: cloneJson(before), after: cloneJson(after) });
  }

  const remainingBefore = () => [...beforeById.entries()].filter(([id]) => !matchedBefore.has(id) && !ambiguousBefore.has(id)).map(([, node]) => node);
  const remainingAfter = () => [...afterById.entries()].filter(([id]) => !matchedAfter.has(id) && !ambiguousAfter.has(id)).map(([, node]) => node);

  for (const property of ['norm_label', 'label']) {
    const beforeGroups = groupBy(remainingBefore(), property);
    const afterGroups = groupBy(remainingAfter(), property);
    for (const key of [...beforeGroups.keys()].filter((value) => afterGroups.has(value)).sort()) {
      const left = beforeGroups.get(key);
      const right = afterGroups.get(key);
      if (left.length === 1 && right.length === 1) {
        const before = left[0];
        const after = right[0];
        const canonical = `fallback:${before.id}`;
        beforeToCanonical.set(before.id, canonical);
        afterToCanonical.set(after.id, canonical);
        matchedBefore.add(before.id);
        matchedAfter.add(after.id);
        const metadataChanges = compareMetadata(before, after, new Set(['id']));
        changed.push({
          identity: { type: property, value: key },
          identity_changed: { before: before.id, after: after.id },
          metadata_changes: metadataChanges,
          before: cloneJson(before),
          after: cloneJson(after)
        });
        addWarning(warnings, 'both', 'NODE_IDENTITY_FALLBACK', `Matched node by unique ${property} after its id changed.`, { before_id: before.id, after_id: after.id, value: key });
      }
    }
  }

  // Remaining cross-snapshot label candidates are deliberately not guessed.
  const leftCandidates = remainingBefore();
  const rightCandidates = remainingAfter();
  for (const property of ['norm_label', 'label']) {
    const leftGroups = groupBy(leftCandidates.filter((node) => !ambiguousBefore.has(node.id)), property);
    const rightGroups = groupBy(rightCandidates.filter((node) => !ambiguousAfter.has(node.id)), property);
    for (const key of [...leftGroups.keys()].filter((value) => rightGroups.has(value)).sort()) {
      const left = leftGroups.get(key);
      const right = rightGroups.get(key);
      const entry = { matched_by: property, value: key, baseline: left.map(cloneJson), after: right.map(cloneJson) };
      ambiguous.push(entry);
      left.forEach((node) => ambiguousBefore.add(node.id));
      right.forEach((node) => ambiguousAfter.add(node.id));
      addWarning(warnings, 'both', 'AMBIGUOUS_NODE_MATCH', `Could not uniquely match nodes by ${property}; candidates are omitted from added/removed results.`, { matched_by: property, value: key, baseline_ids: left.map((node) => node.id), after_ids: right.map((node) => node.id) });
    }
  }

  for (const [id, node] of beforeById) {
    if (!matchedBefore.has(id) && !ambiguousBefore.has(id)) changed.push({ kind: 'removed', node });
  }
  for (const [id, node] of afterById) {
    if (!matchedAfter.has(id) && !ambiguousAfter.has(id)) changed.push({ kind: 'added', node });
  }

  for (const [id] of beforeById) {
    if (!beforeToCanonical.has(id) && !ambiguousBefore.has(id)) beforeToCanonical.set(id, `baseline:${id}`);
  }
  for (const [id] of afterById) {
    if (!afterToCanonical.has(id) && !ambiguousAfter.has(id)) afterToCanonical.set(id, `after:${id}`);
  }

  return { beforeToCanonical, afterToCanonical, ambiguousBefore, ambiguousAfter, ambiguous, changed };
}

function compareMetadata(before, after, excluded) {
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])].filter((key) => !excluded.has(key)).sort();
  const changes = [];
  for (const field of keys) {
    const beforePresent = Object.hasOwn(before, field);
    const afterPresent = Object.hasOwn(after, field);
    if (beforePresent === afterPresent && stableStringify(before[field]) === stableStringify(after[field])) continue;
    changes.push({
      field,
      before_present: beforePresent,
      after_present: afterPresent,
      before: beforePresent ? cloneJson(before[field]) : null,
      after: afterPresent ? cloneJson(after[field]) : null
    });
  }
  return changes;
}

function edgeStructuralKey(edge, canonicalSource, canonicalTarget) {
  return stableStringify([canonicalSource, edge.relation, canonicalTarget]);
}

function edgeMetadata(edge) {
  const { source, target, relation, ...metadata } = edge;
  return metadata;
}

function groupEdges(context, idMap, excludedNodes) {
  const groups = new Map();
  const unresolved = [];
  for (const edge of context.validEdges) {
    if (excludedNodes.has(edge.source) || excludedNodes.has(edge.target)) {
      unresolved.push(edge);
      continue;
    }
    const source = idMap.get(edge.source);
    const target = idMap.get(edge.target);
    if (!source || !target) {
      unresolved.push(edge);
      continue;
    }
    const key = edgeStructuralKey(edge, source, target);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(edge);
  }
  for (const edges of groups.values()) edges.sort((a, b) => compareText(stableStringify(edgeMetadata(a)), stableStringify(edgeMetadata(b))));
  return { groups, unresolved };
}

function compareEdges(beforeContext, afterContext, matching, warnings) {
  const beforeGroups = groupEdges(beforeContext, matching.beforeToCanonical, matching.ambiguousBefore);
  const afterGroups = groupEdges(afterContext, matching.afterToCanonical, matching.ambiguousAfter);
  for (const edge of beforeGroups.unresolved) addWarning(warnings, 'baseline', 'EDGE_WITH_AMBIGUOUS_NODE', 'Edge omitted because an endpoint has ambiguous node identity.', { source: edge.source, target: edge.target, relation: edge.relation });
  for (const edge of afterGroups.unresolved) addWarning(warnings, 'after', 'EDGE_WITH_AMBIGUOUS_NODE', 'Edge omitted because an endpoint has ambiguous node identity.', { source: edge.source, target: edge.target, relation: edge.relation });

  const added = [];
  const removed = [];
  const changed = [];
  const keys = [...new Set([...beforeGroups.groups.keys(), ...afterGroups.groups.keys()])].sort();
  for (const key of keys) {
    const before = beforeGroups.groups.get(key) ?? [];
    const after = afterGroups.groups.get(key) ?? [];
    const common = Math.min(before.length, after.length);
    for (let index = 0; index < common; index += 1) {
      const beforeEdge = before[index];
      const afterEdge = after[index];
      const metadataChanges = compareMetadata(edgeMetadata(beforeEdge), edgeMetadata(afterEdge), new Set());
      if (metadataChanges.length) {
        changed.push({
          identity: { source: beforeEdge.source, target: beforeEdge.target, relation: beforeEdge.relation },
          metadata_changes: metadataChanges,
          before: cloneJson(beforeEdge),
          after: cloneJson(afterEdge)
        });
      }
    }
    for (const edge of before.slice(common)) removed.push({ identity: { source: edge.source, target: edge.target, relation: edge.relation }, edge: cloneJson(edge) });
    for (const edge of after.slice(common)) added.push({ identity: { source: edge.source, target: edge.target, relation: edge.relation }, edge: cloneJson(edge) });
  }
  return { added, removed, changed };
}

function isIncomplete(context, matching, warnings) {
  return context.some((graph) => graph.nodes.some((node) => !node || typeof node !== 'object' || typeof node.id !== 'string' || !node.id) ||
    [...graph.nodesById.values()].some((nodes) => nodes.length > 1) || graph.validEdges.length !== graph.edges.length) ||
    matching.ambiguous.length > 0 || warnings.some((warning) => ['EDGE_WITH_AMBIGUOUS_NODE', 'NODE_IDENTITY_FALLBACK'].includes(warning.code));
}

/** Compare two Graphify graphs, file paths, or GraphContext instances. */
export async function diffGraphs(baselineInput, afterInput) {
  const warnings = [];
  let baselineContext;
  let afterContext;
  try {
    [baselineContext, afterContext] = await Promise.all([asContext(baselineInput, 'baseline'), asContext(afterInput, 'after')]);
  } catch (error) {
    if (error instanceof GraphDiffError || error instanceof GraphContextError) throw error;
    throw new GraphDiffError('GRAPH_LOAD_ERROR', error.message, { cause: error.name });
  }

  addContextWarnings(baselineContext, 'baseline', warnings);
  addContextWarnings(afterContext, 'after', warnings);
  const baselineById = uniqueNodesById(baselineContext, 'baseline', warnings);
  const afterById = uniqueNodesById(afterContext, 'after', warnings);
  const matching = matchNodes(baselineById, afterById, warnings);

  const nodesAdded = matching.changed.filter((item) => item.kind === 'added').map((item) => ({ node: cloneJson(item.node) }));
  const nodesRemoved = matching.changed.filter((item) => item.kind === 'removed').map((item) => ({ node: cloneJson(item.node) }));
  const nodesChanged = matching.changed.filter((item) => !item.kind).sort((a, b) => compareText(stableStringify(a.identity), stableStringify(b.identity)));
  nodesAdded.sort((a, b) => compareText(a.node.id, b.node.id));
  nodesRemoved.sort((a, b) => compareText(a.node.id, b.node.id));
  const edges = compareEdges(baselineContext, afterContext, matching, warnings);
  const incomplete = isIncomplete([baselineContext, afterContext], matching, warnings);
  warnings.sort((a, b) => compareText(`${a.side}:${a.code}:${a.message}`, `${b.side}:${b.code}:${b.message}`));

  return {
    schema_version: RESULT_SCHEMA_VERSION,
    baseline: snapshotInfo(baselineContext),
    after: snapshotInfo(afterContext),
    summary: {
      nodes_added: nodesAdded.length,
      nodes_removed: nodesRemoved.length,
      nodes_changed: nodesChanged.length,
      edges_added: edges.added.length,
      edges_removed: edges.removed.length,
      edges_changed: edges.changed.length,
      ambiguous_node_matches: matching.ambiguous.length,
      complete: !incomplete
    },
    nodes: { added: nodesAdded, removed: nodesRemoved, changed: nodesChanged, ambiguous: matching.ambiguous },
    edges,
    warnings,
    limitations: [
      'This result reports structural and metadata differences only; it does not establish behavioral correctness or successful migration.',
      'Node identity uses unique ids first and falls back only to unique norm_label or label matches; ambiguous fallback candidates are not merged.',
      'Relationships with unresolved or ambiguous endpoints are omitted from edge comparison and reported as warnings.',
      'Source locations and other non-identity attributes are metadata; their changes do not create structural additions or removals.'
    ]
  };
}

/** Convenience wrapper for comparing two native Graphify JSON files. */
export async function diffGraphFiles(baselinePath, afterPath) {
  return diffGraphs(baselinePath, afterPath);
}
