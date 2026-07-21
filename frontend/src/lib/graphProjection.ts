export interface FocusFilterableNode {
  id: string
}

export interface FocusFilterableEdge {
  source_node_id: string
  target_node_id: string
}

/**
 * A reversible filter: with `focusSet === null` (depth 0 / focus off), returns both
 * arrays unchanged. With a focus set, returns only the nodes it contains and only the
 * edges whose *both* endpoints are included — out-of-focus objects are excluded from the
 * canvas entirely (not merely dimmed), so they are neither rendered nor interactive.
 */
export function filterByFocus<N extends FocusFilterableNode, E extends FocusFilterableEdge>(
  nodes: N[],
  edges: E[],
  focusSet: Set<string> | null,
): { nodes: N[]; edges: E[] } {
  if (!focusSet) return { nodes, edges }
  return {
    nodes: nodes.filter((node) => focusSet.has(node.id)),
    edges: edges.filter((edge) => focusSet.has(edge.source_node_id) && focusSet.has(edge.target_node_id)),
  }
}

/**
 * Restricts a node/edge list to only the nodes actually placed on the active canvas, and
 * drops any edge whose source or target is not itself in that placed set. Without this, an
 * edge whose other endpoint has no placement on this canvas (or was excluded by focus) would
 * still be handed to React Flow as a dangling edge with no rendered counterpart node.
 */
export function restrictToPlacedNodes<N extends FocusFilterableNode, E extends FocusFilterableEdge>(
  nodes: N[],
  edges: E[],
  placedNodeIds: Set<string>,
): { nodes: N[]; edges: E[] } {
  const placedNodes = nodes.filter((node) => placedNodeIds.has(node.id))
  const renderedIds = new Set(placedNodes.map((node) => node.id))
  const renderedEdges = edges.filter(
    (edge) => renderedIds.has(edge.source_node_id) && renderedIds.has(edge.target_node_id),
  )
  return { nodes: placedNodes, edges: renderedEdges }
}
