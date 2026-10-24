import { readFile } from 'node:fs/promises';

const dependencyRelations = new Set(['calls', 'imports', 'imports_from']);
const impactRelations = new Set(['calls', 'imports', 'imports_from']);
const observedRelations = new Set(['calls', 'contains', 'imports', 'imports_from']);
const DEFAULT_MAX_DEPTH = 2;
const DEFAULT_MAX_NODES = 100;
const DEFAULT_MAX_EDGES = 100;

export class GraphContextError extends Error {
  constructor(code, message, details = {}) {
    super(message);
    this.name = 'GraphContextError';
    this.code = code;
    this.details = details;
  }
}

function graphWarnings(graph, nodes, edges, edgeKey) {
  const warnings = [];
  if (graph.directed === false) {
    warnings.push('Graph metadata says directed=false; adapter follows verified source-to-target relation semantics for calls/imports/imports_from.');
  }
  if (graph.graph?.graphify_version === undefined) warnings.push('Graphify version metadata is missing.');
  if (graph.hyperedges?.length) warnings.push('Hyperedges are present but are not traversed by this adapter.');
  if (edgeKey === 'edges') warnings.push('Using raw extraction edges; clustering metadata and final links may differ.');
  if (graph.links && graph.edges) warnings.push('Both links and edges are present; using links.');
  const unknownRelations = [...new Set(edges.filter((edge) => typeof edge?.relation === 'string' && !observedRelations.has(edge.relation)).map((edge) => edge.relation))];
  if (unknownRelations.length) warnings.push(`Unverified relationship type(s) are preserved but not traversed: ${unknownRelations.join(', ')}.`);
  const edgesWithoutConfidence = edges.filter((edge) => edge && typeof edge === 'object' && edge.confidence === undefined && edge.confidence_score === undefined).length;
  if (edgesWithoutConfidence) warnings.push(`${edgesWithoutConfidence} edge(s) lack confidence metadata; no confidence value was inferred.`);

  for (const [index, node] of nodes.entries()) {
    if (!node || typeof node !== 'object' || typeof node.id !== 'string' || !node.id) {
      warnings.push(`Node at index ${index} has no usable id and cannot be resolved.`);
    }
  }
  for (const [index, edge] of edges.entries()) {
    if (!edge || typeof edge !== 'object') {
      warnings.push(`Edge at index ${index} is not an object and was ignored.`);
      continue;
    }
    if (typeof edge.source !== 'string' || typeof edge.target !== 'string' || typeof edge.relation !== 'string') {
      warnings.push(`Edge at index ${index} is missing source, target, or relation and was ignored.`);
    }
  }
  return warnings;
}

function shortNode(node) {
  const result = { id: node.id };
  for (const key of ['label', 'source_file', 'source_location', 'file_type', 'type', '_callable', 'norm_label', 'community', 'community_name', '_origin']) {
    if (node[key] !== undefined) result[key] = node[key];
  }
  return result;
}

function shortEdge(edge) {
  const result = { source: edge.source, target: edge.target, relation: edge.relation };
  for (const key of ['source_file', 'source_location', 'context', '_origin', 'confidence', 'confidence_score', 'weight']) {
    if (edge[key] !== undefined) result[key] = edge[key];
  }
  return result;
}

function checkedLimit(value, fallback, name) {
  const limit = value ?? fallback;
  if (!Number.isInteger(limit) || limit < 0) {
    throw new GraphContextError('INVALID_OPTION', `${name} must be a non-negative integer.`, { name, value });
  }
  return limit;
}

export async function loadGraph(graphPath) {
  let parsed;
  try {
    parsed = JSON.parse(await readFile(graphPath, 'utf8'));
  } catch (error) {
    throw new GraphContextError('GRAPH_READ_ERROR', `Could not read Graphify graph at ${graphPath}: ${error.message}`, { graphPath });
  }
  if (!parsed || typeof parsed !== 'object' || !Array.isArray(parsed.nodes)) {
    throw new GraphContextError('INVALID_GRAPH', 'Graph must be an object with a nodes array.', { graphPath });
  }
  const edgeKey = Array.isArray(parsed.links) ? 'links' : Array.isArray(parsed.edges) ? 'edges' : null;
  if (!edgeKey) {
    throw new GraphContextError('INVALID_GRAPH', 'Graph must contain a links or edges array.', { graphPath });
  }
  return new GraphContext(parsed, graphPath, edgeKey);
}

export class GraphContext {
  constructor(graph, graphPath = undefined, edgeKey = Array.isArray(graph.links) ? 'links' : 'edges') {
    this.graphPath = graphPath;
    this.graph = graph;
    this.nodes = graph.nodes;
    this.edges = graph[edgeKey];
    this.edgeKey = edgeKey;
    this.warnings = graphWarnings(graph, this.nodes, this.edges, edgeKey);
    this.nodesById = new Map();
    this.validEdges = [];
    this.nodes.forEach((node, index) => {
      if (!node || typeof node !== 'object' || typeof node.id !== 'string' || !node.id) return;
      const prior = this.nodesById.get(node.id);
      if (prior) {
        prior.push(node);
        this.warnings.push(`Duplicate node id "${node.id}" at node index ${index}; references to this id are ambiguous.`);
      } else {
        this.nodesById.set(node.id, [node]);
      }
    });
    this.edges.forEach((edge, index) => {
      if (!edge || typeof edge !== 'object' || typeof edge.source !== 'string' || typeof edge.target !== 'string' || typeof edge.relation !== 'string') return;
      const sourceCount = this.nodesById.get(edge.source)?.length ?? 0;
      const targetCount = this.nodesById.get(edge.target)?.length ?? 0;
      if (sourceCount !== 1 || targetCount !== 1) {
        this.warnings.push(`Edge at index ${index} has unresolved or ambiguous endpoint(s): ${edge.source} → ${edge.target}.`);
        return;
      }
      this.validEdges.push(edge);
    });
  }

  findNode(target) {
    if (typeof target !== 'string' || !target.trim()) {
      throw new GraphContextError('INVALID_TARGET', 'Target must be a non-empty string.', { target });
    }
    const query = target.trim();
    const byId = this.nodesById.get(query);
    if (byId?.length === 1) return byId[0];
    if (byId?.length > 1) {
      throw new GraphContextError('AMBIGUOUS_TARGET', `Target "${query}" matches duplicate node identifiers.`, { target: query, candidates: byId.map(shortNode) });
    }

    const matches = this.nodes.filter((node) => node && typeof node === 'object' &&
      (node.label === query || node.norm_label === query));
    const unique = [...new Map(matches.filter((node) => typeof node.id === 'string').map((node) => [node.id, node])).values()];
    if (unique.length === 1) return unique[0];
    if (unique.length > 1) {
      throw new GraphContextError('AMBIGUOUS_TARGET', `Target "${query}" matches multiple graph nodes.`, { target: query, candidates: unique.map(shortNode) });
    }
    throw new GraphContextError('NODE_NOT_FOUND', `No graph node matches "${query}" by id, label, or norm_label. This Graphify schema has no verified name field.`, { target: query });
  }

  getDependencies(target) {
    const node = typeof target === 'string' ? this.findNode(target) : target;
    return this.validEdges
      .filter((edge) => edge.source === node.id && dependencyRelations.has(edge.relation))
      .map((edge) => ({ node: this.nodeFor(edge.target), edge: shortEdge(edge) }));
  }

  getDependents(target) {
    const node = typeof target === 'string' ? this.findNode(target) : target;
    return this.validEdges
      .filter((edge) => edge.target === node.id && dependencyRelations.has(edge.relation))
      .map((edge) => ({ node: this.nodeFor(edge.source), edge: shortEdge(edge) }));
  }

  getImpact(target, options = {}) {
    const start = typeof target === 'string' ? this.findNode(target) : target;
    const maxDepth = checkedLimit(options.maxDepth, DEFAULT_MAX_DEPTH, 'maxDepth');
    const maxNodes = checkedLimit(options.maxNodes, DEFAULT_MAX_NODES, 'maxNodes');
    const relations = options.relations ?? [...impactRelations];
    if (!Array.isArray(relations) || relations.some((relation) => !impactRelations.has(relation))) {
      throw new GraphContextError('INVALID_OPTION', 'relations may contain only verified impact relations: calls, imports, imports_from.', { relations });
    }

    const visited = new Set([start.id]);
    const queue = [{ node: start, depth: 0 }];
    const impact = [];
    let truncated = false;
    while (queue.length) {
      const current = queue.shift();
      if (current.depth >= maxDepth) continue;
      const incoming = this.validEdges.filter((edge) => edge.target === current.node.id && relations.includes(edge.relation));
      for (const edge of incoming) {
        if (visited.has(edge.source)) continue;
        if (impact.length >= maxNodes) {
          truncated = true;
          break;
        }
        const node = this.nodeFor(edge.source);
        if (!node) continue;
        const depth = current.depth + 1;
        visited.add(node.id);
        impact.push({ node: shortNode(node), depth, edge: shortEdge(edge) });
        queue.push({ node, depth });
      }
      if (truncated) break;
    }
    const direct = impact.filter((item) => item.depth === 1);
    const transitive = impact.filter((item) => item.depth > 1);
    return {
      target: shortNode(start),
      maxDepth,
      relations: [...relations],
      direct,
      transitive,
      nodes: impact,
      truncated,
      warnings: truncated ? [`Impact truncated at ${maxNodes} nodes.`] : []
    };
  }

  getMigrationContext(target, options = {}) {
    const node = this.findNode(target);
    const maxEdges = checkedLimit(options.maxEdges, DEFAULT_MAX_EDGES, 'maxEdges');
    const dependencies = this.getDependencies(node);
    const directDependents = this.getDependents(node);
    const callers = directDependents.filter(({ edge }) => edge.relation === 'calls');
    const impact = this.getImpact(node, options);
    const warnings = [...this.warnings, ...impact.warnings];
    if (!node.source_file || !node.source_location) {
      warnings.push(`Target ${node.id} has incomplete source location metadata.`);
    }
    const includeBounded = (items, label) => {
      if (items.length <= maxEdges) return items;
      warnings.push(`${label} limited to ${maxEdges} entries.`);
      return items.slice(0, maxEdges);
    };
    const references = new Set();
    if (node.source_file) references.add(node.source_file);
    for (const item of [...dependencies, ...directDependents, ...impact.nodes]) {
      const file = item.node?.source_file ?? item.source_file;
      if (file) references.add(file);
      if (item.edge?.source_file) references.add(item.edge.source_file);
      if (item.edge && (!item.edge.source_file || !item.edge.source_location || !item.edge._origin || (!item.edge.confidence && item.edge.confidence_score === undefined))) {
        warnings.push(`Evidence edge ${item.edge.source} -${item.edge.relation}-> ${item.edge.target} has incomplete provenance or confidence metadata.`);
      }
    }
    if (!node.source_file) warnings.push('No source reference is available for the target node.');

    return {
      graph: {
        path: this.graphPath,
        graphify_version: this.graph.graph?.graphify_version,
        schema_version: this.graph.graph?.schema_version,
        node_count: this.nodes.length,
        edge_count: this.edges.length,
        edge_key: this.edgeKey
      },
      target: shortNode(node),
      dependencies: includeBounded(dependencies, 'Dependencies'),
      direct_dependents: includeBounded(directDependents, 'Direct dependents'),
      callers: includeBounded(callers, 'Callers'),
      impact: {
        maxDepth: impact.maxDepth,
        relations: impact.relations,
        direct: includeBounded(impact.direct, 'Direct impact'),
        transitive: includeBounded(impact.transitive, 'Transitive impact'),
        truncated: impact.truncated
      },
      evidence: {
        source_references: [...references],
        target_node: node,
        dependency_edges: dependencies.map((item) => item.edge).slice(0, maxEdges),
        dependent_edges: directDependents.map((item) => item.edge).slice(0, maxEdges),
        caller_edges: callers.map((item) => item.edge).slice(0, maxEdges),
        impact_edges: impact.nodes.map((item) => item.edge).slice(0, maxEdges)
      },
      warnings: [...new Set(warnings)]
    };
  }

  nodeFor(id) {
    const matches = this.nodesById.get(id);
    return matches?.length === 1 ? shortNode(matches[0]) : undefined;
  }
}
