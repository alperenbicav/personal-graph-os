export interface EdgeEndpoints {
  source_node_id: string
  target_node_id: string
}

/**
 * Breadth-first expansion from `focusNodeId` up to `depth` hops, following edges in
 * either direction. `depth <= 0` returns `null`, meaning "no filter" — the caller should
 * treat that as showing everything rather than an empty set.
 */
export function neighborhoodWithinDepth(
  edges: EdgeEndpoints[],
  focusNodeId: string,
  depth: number,
): Set<string> | null {
  if (depth <= 0) return null

  let frontier = new Set([focusNodeId])
  const seen = new Set([focusNodeId])

  for (let hop = 0; hop < depth; hop++) {
    const next = new Set<string>()
    for (const edge of edges) {
      if (frontier.has(edge.source_node_id) && !seen.has(edge.target_node_id)) {
        next.add(edge.target_node_id)
      }
      if (frontier.has(edge.target_node_id) && !seen.has(edge.source_node_id)) {
        next.add(edge.source_node_id)
      }
    }
    if (next.size === 0) break
    next.forEach((id) => seen.add(id))
    frontier = next
  }

  return seen
}
