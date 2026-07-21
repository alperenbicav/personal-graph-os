import { describe, expect, it } from 'vitest'
import { filterByFocus, restrictToPlacedNodes } from './graphProjection'

const nodes = [{ id: 'a' }, { id: 'b' }, { id: 'c' }]
const edges = [
  { source_node_id: 'a', target_node_id: 'b' },
  { source_node_id: 'b', target_node_id: 'c' },
]

describe('filterByFocus', () => {
  it('returns everything unchanged when focus is off (null)', () => {
    const result = filterByFocus(nodes, edges, null)
    expect(result.nodes).toBe(nodes)
    expect(result.edges).toBe(edges)
  })

  it('excludes nodes outside the focus set entirely', () => {
    const result = filterByFocus(nodes, edges, new Set(['a', 'b']))
    expect(result.nodes.map((n) => n.id)).toEqual(['a', 'b'])
  })

  it('excludes an edge unless both endpoints are in the focus set', () => {
    const result = filterByFocus(nodes, edges, new Set(['a', 'b']))
    expect(result.edges).toEqual([{ source_node_id: 'a', target_node_id: 'b' }])
  })

  it('excludes every edge when the focus set is a single isolated node', () => {
    const result = filterByFocus(nodes, edges, new Set(['a']))
    expect(result.edges).toEqual([])
  })

  it('restores the complete graph when focus is toggled back off', () => {
    const focused = filterByFocus(nodes, edges, new Set(['a']))
    expect(focused.nodes).toHaveLength(1)
    const restored = filterByFocus(nodes, edges, null)
    expect(restored.nodes).toEqual(nodes)
    expect(restored.edges).toEqual(edges)
  })
})

describe('restrictToPlacedNodes', () => {
  it('drops an edge whose other endpoint has no placement on this canvas, focus off', () => {
    // 'c' is a real workspace node/edge endpoint, but never placed on this canvas.
    const placedOnCanvas = new Set(['a', 'b'])
    const result = restrictToPlacedNodes(nodes, edges, placedOnCanvas)
    expect(result.nodes.map((n) => n.id)).toEqual(['a', 'b'])
    expect(result.edges).toEqual([{ source_node_id: 'a', target_node_id: 'b' }])
  })

  it('drops an edge whose other endpoint has no placement on this canvas, focus enabled', () => {
    const focused = filterByFocus(nodes, edges, new Set(['a', 'b', 'c']))
    const placedOnCanvas = new Set(['a', 'b'])
    const result = restrictToPlacedNodes(focused.nodes, focused.edges, placedOnCanvas)
    expect(result.nodes.map((n) => n.id)).toEqual(['a', 'b'])
    expect(result.edges).toEqual([{ source_node_id: 'a', target_node_id: 'b' }])
  })

  it('keeps an edge when both endpoints are placed', () => {
    const placedOnCanvas = new Set(['a', 'b', 'c'])
    const result = restrictToPlacedNodes(nodes, edges, placedOnCanvas)
    expect(result.edges).toEqual(edges)
  })
})
