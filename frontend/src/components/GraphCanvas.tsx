import { useCallback, useEffect, useMemo } from 'react'
import {
  Background,
  BackgroundVariant,
  type Connection,
  Controls,
  Panel,
  useReactFlow,
  useStore,
  type Edge,
  type Node,
  type NodeMouseHandler,
  type OnNodeDrag,
  ReactFlow,
  useEdgesState,
  useNodesState,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { filterByFocus, restrictToPlacedNodes } from '../lib/graphProjection'
import type {
  Canvas,
  CanvasPlacement,
  EdgeType,
  GraphEdge,
  GraphNode,
  NodeType,
  StatusDefinition,
} from '../types'
import { CanvasObjectList } from './CanvasObjectList'
import { TypedNode, type TypedNodeData } from './nodes/TypedNode'

/** Reset zoom/pan to fit every placed node (EP-2026-013 ST-10 review P3-10). */
function FitViewButton() {
  const { fitView } = useReactFlow()
  return (
    <Panel position="bottom-right">
      <button
        type="button"
        className="zoom-badge zoom-fit-button"
        aria-label="Fit view"
        title="Fit to canvas"
        onClick={() => void fitView({ padding: 0.2, duration: 200 })}
      >
        ⊡ Fit
      </button>
    </Panel>
  )
}


/** Live zoom percentage in the canvas corner (EP-2026-013 ST-08). */
function ZoomBadge() {
  const zoom = useStore((state) => state.transform[2])
  return (
    <Panel position="bottom-right">
      <span className="zoom-badge">{Math.round(zoom * 100)}%</span>
    </Panel>
  )
}

const NODE_TYPES = { typedNode: TypedNode }

interface GraphCanvasProps {
  canvas: Canvas | null
  nodes: GraphNode[]
  edges: GraphEdge[]
  placements: CanvasPlacement[]
  nodeTypeById: Map<string, NodeType>
  edgeTypeById: Map<string, EdgeType>
  statusById: Map<string, StatusDefinition>
  selectedNodeId: string | null
  focusSet: Set<string> | null
  onSelectNode: (nodeId: string | null) => void
  onMovePlacement: (placementId: string, positionX: number, positionY: number) => void
  onRequestConnect: (sourceNodeId: string, targetNodeId: string) => void
}

function kindSlugFor(nodeType: NodeType | undefined): string {
  return nodeType ? nodeType.name.toLowerCase() : 'unknown'
}

export function GraphCanvas({
  canvas,
  nodes,
  edges,
  placements,
  nodeTypeById,
  edgeTypeById,
  statusById,
  selectedNodeId,
  focusSet,
  onSelectNode,
  onMovePlacement,
  onRequestConnect,
}: GraphCanvasProps) {
  const placementByNodeId = useMemo(() => {
    const map = new Map<string, CanvasPlacement>()
    placements.forEach((placement) => map.set(placement.node_id, placement))
    return map
  }, [placements])

  const placedNodeIds = useMemo(() => new Set(placementByNodeId.keys()), [placementByNodeId])

  const { nodes: focusedNodes, edges: focusedEdges } = useMemo(
    () => filterByFocus(nodes, edges, focusSet),
    [nodes, edges, focusSet],
  )

  // Every rendered edge's endpoints must themselves be rendered nodes on this canvas — an
  // edge whose other endpoint has no placement here (or was excluded by focus) would
  // otherwise reach React Flow as a dangling edge with no counterpart node.
  const { nodes: renderedNodes, edges: renderedEdges } = useMemo(
    () => restrictToPlacedNodes(focusedNodes, focusedEdges, placedNodeIds),
    [focusedNodes, focusedEdges, placedNodeIds],
  )

  // Signature-based sync keys: re-derive the React Flow node list whenever the *set* of
  // nodes/placements changes, or when displayed content (title/status) changes — not on
  // every position tick, since drag position is owned by React Flow's own state between
  // syncs and persisted explicitly in `onMovePlacement`.
  const nodeContentSignature = renderedNodes.map((n) => `${n.id}:${n.title}:${n.status_id}`).join(',')
  const placementSignature = placements
    .map((p) => `${p.id}:${p.position_x},${p.position_y}`)
    .join(',')

  const initialRfNodes = useMemo<Node[]>(() => {
    return renderedNodes.map((node) => {
      const placement = placementByNodeId.get(node.id)!
      const nodeType = nodeTypeById.get(node.node_type_id)
      const status = node.status_id ? statusById.get(node.status_id) : undefined
      const data: TypedNodeData = {
        title: node.title,
        kindLabel: nodeType?.name ?? 'Object',
        kindSlug: kindSlugFor(nodeType),
        statusLabel: status?.name ?? null,
      }
      return {
        id: node.id,
        type: 'typedNode',
        position: { x: placement.position_x, y: placement.position_y },
        data,
      }
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeContentSignature, placementSignature, nodeTypeById, statusById, placementByNodeId])

  const initialRfEdges = useMemo<Edge[]>(() => {
    return renderedEdges.map((edge) => {
      const edgeType = edgeTypeById.get(edge.edge_type_id)
      return {
        id: edge.id,
        source: edge.source_node_id,
        target: edge.target_node_id,
        label: edgeType?.name ?? '',
        style: { stroke: edgeType?.color_hex ?? 'var(--slate-faint)' },
        labelStyle: { fill: 'var(--slate)', fontSize: 10 },
        labelBgStyle: { fill: 'var(--paper-raised)' },
      }
    })
  }, [renderedEdges, edgeTypeById])

  const [rfNodes, setRfNodes, onNodesChange] = useNodesState(initialRfNodes)
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState(initialRfEdges)

  useEffect(() => setRfNodes(initialRfNodes), [initialRfNodes, setRfNodes])
  useEffect(() => setRfEdges(initialRfEdges), [initialRfEdges, setRfEdges])

  useEffect(() => {
    setRfNodes((current) =>
      current.map((node) => ({ ...node, selected: node.id === selectedNodeId })),
    )
  }, [selectedNodeId, setRfNodes])

  const handleNodeClick = useCallback<NodeMouseHandler>(
    (_event, node) => onSelectNode(node.id),
    [onSelectNode],
  )

  const handleNodeDragStop = useCallback<OnNodeDrag<Node>>(
    (_event, node) => {
      const placement = placementByNodeId.get(node.id)
      if (!placement) return
      onMovePlacement(placement.id, node.position.x, node.position.y)
    },
    [placementByNodeId, onMovePlacement],
  )

  const handlePaneClick = useCallback(() => onSelectNode(null), [onSelectNode])

  const handleConnect = useCallback(
    (connection: Connection) => {
      if (!connection.source || !connection.target) return
      onRequestConnect(connection.source, connection.target)
    },
    [onRequestConnect],
  )

  if (!canvas) {
    return <div className="empty-canvas-hint">Loading canvas…</div>
  }

  if (nodes.length === 0) {
    return (
      <div className="empty-canvas-hint">
        Nothing captured yet on “{canvas.name}.” Use the capture field in the canvas toolbar
        to add your first object.
      </div>
    )
  }

  if (focusSet && renderedNodes.length === 0) {
    return (
      <div className="empty-canvas-hint">
        No objects within the selected neighborhood depth. Reduce the depth or turn focus off
        to see the full canvas again.
      </div>
    )
  }

  return (
    <div className="graph-canvas-root">
      <CanvasObjectList
        nodes={renderedNodes}
        nodeTypeById={nodeTypeById}
        selectedNodeId={selectedNodeId}
        onSelectNode={onSelectNode}
      />
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={NODE_TYPES}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={handleNodeClick}
        onNodeDragStop={handleNodeDragStop}
        onPaneClick={handlePaneClick}
        onConnect={handleConnect}
        fitView
        minZoom={0.4}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} color="var(--line)" gap={22} size={1.4} />
        <Controls showInteractive={false} />
        <ZoomBadge />
        <FitViewButton />
      </ReactFlow>
    </div>
  )
}
