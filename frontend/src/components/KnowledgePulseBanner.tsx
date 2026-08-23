import { useEffect, useMemo, useState } from 'react'
import type { GraphEdge, GraphNode, WorkItem } from '../types'

interface KnowledgePulseBannerProps {
  nodes?: GraphNode[]
  edges?: GraphEdge[]
  workItems?: WorkItem[]
}

function useCountUp(target: number, durationMs = 500): number {
  const [current, setCurrent] = useState(0)

  useEffect(() => {
    if (target === 0) {
      setCurrent(0)
      return
    }
    let startTimestamp: number | null = null
    let animationFrameId: number
    const startVal = 0

    function step(timestamp: number) {
      if (!startTimestamp) startTimestamp = timestamp
      const progress = Math.min((timestamp - startTimestamp) / durationMs, 1)
      const ease = 1 - Math.pow(1 - progress, 2)
      setCurrent(Math.round(startVal + (target - startVal) * ease))
      if (progress < 1) {
        animationFrameId = requestAnimationFrame(step)
      }
    }

    animationFrameId = requestAnimationFrame(step)
    return () => cancelAnimationFrame(animationFrameId)
  }, [target, durationMs])

  return current
}

export function KnowledgePulseBanner({
  nodes = [],
  edges = [],
  workItems = [],
}: KnowledgePulseBannerProps) {
  const stats = useMemo(() => {
    const nodeCount = nodes.length
    const edgeCount = edges.length

    const connectedNodeIds = new Set<string>()
    for (const edge of edges) {
      connectedNodeIds.add(edge.source_node_id)
      connectedNodeIds.add(edge.target_node_id)
    }
    const connectedRatio =
      nodeCount === 0 ? 0 : Math.round((connectedNodeIds.size / nodeCount) * 100)

    const completedTasks = workItems.filter(
      (item) => item.status === 'done' || item.status === 'production',
    ).length

    return {
      nodeCount,
      edgeCount,
      connectedRatio,
      completedTasks,
    }
  }, [nodes, edges, workItems])

  const animatedNodes = useCountUp(stats.nodeCount)
  const animatedEdges = useCountUp(stats.edgeCount)
  const animatedRatio = useCountUp(stats.connectedRatio)
  const animatedTasks = useCountUp(stats.completedTasks)

  return (
    <div className="knowledge-pulse-banner" aria-label="Knowledge pulse and workspace metrics">
      <div className="pulse-chip">
        <span className="pulse-chip-icon" aria-hidden="true">
          🌐
        </span>
        <div className="pulse-chip-content">
          <span className="pulse-chip-number">{animatedNodes}</span>
          <span className="pulse-chip-label">Graph Nodes</span>
          <span className="pulse-chip-sub">Workspace entities</span>
        </div>
      </div>

      <div className="pulse-chip">
        <span className="pulse-chip-icon" aria-hidden="true">
          🔗
        </span>
        <div className="pulse-chip-content">
          <span className="pulse-chip-number">{animatedEdges}</span>
          <span className="pulse-chip-label">Relations</span>
          <span className="pulse-chip-sub">Connected edges</span>
        </div>
      </div>

      <div className="pulse-chip">
        <span className="pulse-chip-icon" aria-hidden="true">
          ⚡
        </span>
        <div className="pulse-chip-content">
          <span className="pulse-chip-number">{animatedRatio}%</span>
          <span className="pulse-chip-label">Connectivity</span>
          <span className="pulse-chip-sub">Nodes with &ge;1 link</span>
        </div>
      </div>

      <div className="pulse-chip">
        <span className="pulse-chip-icon" aria-hidden="true">
          ✅
        </span>
        <div className="pulse-chip-content">
          <span className="pulse-chip-number">{animatedTasks}</span>
          <span className="pulse-chip-label">Tasks Shipped</span>
          <span className="pulse-chip-sub">Done &amp; production</span>
        </div>
      </div>
    </div>
  )
}
