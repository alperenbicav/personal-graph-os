import { describe, expect, it } from 'vitest'
import { neighborhoodWithinDepth } from './neighborhood'

// A -> B -> C -> D, plus an unrelated E -> F chain.
const edges = [
  { source_node_id: 'a', target_node_id: 'b' },
  { source_node_id: 'b', target_node_id: 'c' },
  { source_node_id: 'c', target_node_id: 'd' },
  { source_node_id: 'e', target_node_id: 'f' },
]

describe('neighborhoodWithinDepth', () => {
  it('returns null (no filter) when depth is zero or negative', () => {
    expect(neighborhoodWithinDepth(edges, 'a', 0)).toBeNull()
    expect(neighborhoodWithinDepth(edges, 'a', -1)).toBeNull()
  })

  it('includes only the focus node at depth 0 semantics is handled by caller, but 1 hop reaches direct neighbors', () => {
    const result = neighborhoodWithinDepth(edges, 'b', 1)
    expect(result).toEqual(new Set(['b', 'a', 'c']))
  })

  it('expands two hops in both directions', () => {
    const result = neighborhoodWithinDepth(edges, 'b', 2)
    expect(result).toEqual(new Set(['b', 'a', 'c', 'd']))
  })

  it('never includes nodes from an unrelated component', () => {
    const result = neighborhoodWithinDepth(edges, 'a', 3)
    expect(result?.has('e')).toBe(false)
    expect(result?.has('f')).toBe(false)
  })

  it('stops early and does not throw when the frontier runs dry before reaching max depth', () => {
    const result = neighborhoodWithinDepth(edges, 'd', 3)
    expect(result).toEqual(new Set(['d', 'c', 'b', 'a']))
  })

  it('handles a focus node with no edges at all', () => {
    const result = neighborhoodWithinDepth(edges, 'isolated', 2)
    expect(result).toEqual(new Set(['isolated']))
  })
})
