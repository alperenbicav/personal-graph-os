import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './App.css'
import * as api from './api/client'
import { ActivityView } from './components/ActivityView'
import { CanvasRail } from './components/CanvasRail'
import { ConnectEdgeModal, type PendingConnection } from './components/ConnectEdgeModal'
import { CreateNodeControl } from './components/CreateNodeControl'
import { EnrichmentDetailPanel } from './components/EnrichmentDetailPanel'
import { GraphCanvas } from './components/GraphCanvas'
import { Inspector, type RelationRow } from './components/Inspector'
import { NavTabs, type AppView } from './components/NavTabs'
import { PlaceExistingNodeControl } from './components/PlaceExistingNodeControl'
import { RepositoriesView } from './components/RepositoriesView'
import { ResearchDetailPanel } from './components/ResearchDetailPanel'
import { ResearchView } from './components/ResearchView'
import { SearchView } from './components/SearchView'
import { TasksView } from './components/TasksView'
import { TopBar } from './components/TopBar'
import { UnlockScreen } from './components/UnlockScreen'
import { WikiView } from './components/WikiView'
import { onSessionUnauthorized, restoreSession } from './api/session'
import {
  WorkflowChainPanel,
  type WorkflowChainAdvanceInput,
} from './components/WorkflowChainPanel'
import { messageFor } from './lib/errors'
import { neighborhoodWithinDepth } from './lib/neighborhood'
import { NEXT_WORKFLOW_STEP } from './lib/workflowChain'
import { SchemaEditor } from './components/SchemaEditor'
import type {
  Canvas,
  CanvasPlacement,
  GraphEdge,
  GraphNode,
  RelatedNode,
  RepositoryLabel,
  ResearchDashboard,
  Resource,
  ResourceKind,
  StatusDefinition,
  WikiDocument,
  WorkItem,
  WorkflowChainStep,
  Workspace,
} from './types'

const MAX_FOCUS_DEPTH = 3

function randomSpawnPosition() {
  return { x: 220 + Math.random() * 120, y: 200 + Math.random() * 120 }
}

function App() {
  const [isUnlocked, setIsUnlocked] = useState(() => Boolean(restoreSession()))
  const [workspace, setWorkspace] = useState<Workspace | null>(null)
  const [nodes, setNodes] = useState<GraphNode[]>([])
  const [edges, setEdges] = useState<GraphEdge[]>([])
  const [canvases, setCanvases] = useState<Canvas[]>([])
  const [activeCanvasId, setActiveCanvasId] = useState<string | null>(null)
  const [placements, setPlacements] = useState<CanvasPlacement[]>([])
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [focusDepth, setFocusDepth] = useState(0)
  const [pendingConnection, setPendingConnection] = useState<PendingConnection | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [isSchemaEditorOpen, setIsSchemaEditorOpen] = useState(false)

  const [activeView, setActiveView] = useState<AppView>('graph')
  const [resources, setResources] = useState<Resource[]>([])
  const [workItems, setWorkItems] = useState<WorkItem[]>([])
  const [documents, setDocuments] = useState<WikiDocument[]>([])
  const [researchDashboard, setResearchDashboard] = useState<ResearchDashboard | null>(null)
  const [selectedWorkItemId, setSelectedWorkItemId] = useState<string | null>(null)

  // Per-placement move sequence: guards against an older, now-superseded save request
  // rolling back a position that a newer move already replaced (or is still in flight).
  const placementMoveSeqRef = useRef(new Map<string, number>())
  // The last position each placement is known to have actually persisted (from a fresh
  // load, a successful create, or a successful move) — independent of any in-flight
  // optimistic position. A failed move reverts here, never to a merely-prior optimistic
  // value that itself was never confirmed to have persisted.
  const lastPersistedPositionRef = useRef(new Map<string, { x: number; y: number }>())

  const recordPersisted = useCallback((placement: CanvasPlacement) => {
    lastPersistedPositionRef.current.set(placement.id, {
      x: placement.position_x,
      y: placement.position_y,
    })
  }, [])

  // Undo (ST-07.3) can change any entity type unpredictably, so rather than guessing which
  // slice of state to patch, this re-fetches the same node/edge/resource/placement data the
  // initial bootstrap loads, keeping every view consistent with canonical state afterward.
  const reloadGraphData = useCallback(async (workspaceId: string, canvasId: string | null) => {
    const [nodesResponse, edgesResponse, resourcesResponse] = await Promise.all([
      api.listNodes(workspaceId),
      api.listEdges(workspaceId),
      api.listResources(workspaceId),
    ])
    setNodes(nodesResponse)
    setEdges(edgesResponse)
    setResources(resourcesResponse)
    if (canvasId) {
      const placementsResponse = await api.listPlacements(canvasId)
      placementsResponse.forEach(recordPersisted)
      setPlacements(placementsResponse)
    }
  }, [recordPersisted])

  useEffect(() => {
    onSessionUnauthorized(() => setIsUnlocked(false))
  }, [])

  useEffect(() => {
    if (!isUnlocked) return
    let cancelled = false
    async function bootstrap() {
      setLoadError(null)
      try {
        const workspaceResponse = await api.getWorkspace()
        if (cancelled) return
        setWorkspace(workspaceResponse)

        const [nodesResponse, edgesResponse, canvasesResponse, resourcesResponse] = await Promise.all([
          api.listNodes(workspaceResponse.id),
          api.listEdges(workspaceResponse.id),
          api.listCanvases(workspaceResponse.id),
          api.listResources(workspaceResponse.id),
        ])
        if (cancelled) return
        setNodes(nodesResponse)
        setEdges(edgesResponse)
        setCanvases(canvasesResponse)
        setActiveCanvasId(canvasesResponse[0]?.id ?? null)
        setResources(resourcesResponse)
        const [workItemsResponse, documentsResponse] = await Promise.all([
          api.listWorkItems(workspaceResponse.id),
          api.listDocuments(workspaceResponse.id),
        ])
        if (cancelled) return
        setWorkItems(workItemsResponse ?? [])
        setDocuments(documentsResponse ?? [])
      } catch (error) {
        if (!cancelled) setLoadError(messageFor(error))
      }
    }
    bootstrap()
    return () => {
      cancelled = true
    }
  }, [isUnlocked])

  useEffect(() => {
    if (!activeCanvasId) return
    let cancelled = false
    // Clear immediately so a slow or failed request for the newly active canvas can never
    // leave the previous canvas's placements rendered (and writable via drag) under this id.
    setPlacements([])
    api
      .listPlacements(activeCanvasId)
      .then((response) => {
        if (cancelled) return
        response.forEach(recordPersisted)
        setPlacements(response)
      })
      .catch((error) => {
        if (!cancelled) setActionError(`Could not load this canvas's placements: ${messageFor(error)}`)
      })
    return () => {
      cancelled = true
    }
  }, [activeCanvasId, recordPersisted])

  // The one refresh path the Tasks/Research workspaces share: re-fetch whichever view is
  // currently active from the backend so an edit made anywhere is reflected the next time
  // this runs rather than each view keeping its own stale copy.
  const refreshActiveViewData = useCallback(
    async (workspaceId: string, view: AppView) => {
      try {
        if (view === 'tasks') {
          setWorkItems((await api.listWorkItems(workspaceId)) ?? [])
        } else if (view === 'research') {
          setResearchDashboard(await api.getResearchDashboard(workspaceId))
        }
      } catch (error) {
        setActionError(`Could not load this view: ${messageFor(error)}`)
      }
    },
    [],
  )

  useEffect(() => {
    if (!workspace) return
    refreshActiveViewData(workspace.id, activeView)
  }, [workspace, activeView, refreshActiveViewData])

  const nodeTypeById = useMemo(
    () => new Map(workspace?.node_types.map((nodeType) => [nodeType.id, nodeType]) ?? []),
    [workspace],
  )

  const edgeTypeById = useMemo(
    () => new Map(workspace?.edge_types.map((edgeType) => [edgeType.id, edgeType]) ?? []),
    [workspace],
  )

  const statusById = useMemo(() => {
    const map = new Map<string, StatusDefinition>()
    workspace?.node_types.forEach((nodeType) =>
      nodeType.status_definitions.forEach((status) => map.set(status.id, status)),
    )
    return map
  }, [workspace])

  const nodesById = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes])

  const activeCanvas = useMemo(
    () => canvases.find((canvas) => canvas.id === activeCanvasId) ?? null,
    [canvases, activeCanvasId],
  )

  const visibleNodes = useMemo(() => nodes.filter((node) => !node.is_archived), [nodes])

  const placedNodeIds = useMemo(() => new Set(placements.map((p) => p.node_id)), [placements])
  const unplacedNodes = useMemo(
    () => visibleNodes.filter((node) => !placedNodeIds.has(node.id)),
    [visibleNodes, placedNodeIds],
  )

  // Candidates for an `object_reference` field's node-selector control: every other visible
  // node in the workspace (a node can't reference itself).
  const referenceableNodes = useMemo(
    () =>
      visibleNodes
        .filter((node) => node.id !== selectedNodeId)
        .map((node) => ({ id: node.id, title: node.title })),
    [visibleNodes, selectedNodeId],
  )

  const focusSet = useMemo(() => {
    if (!selectedNodeId || focusDepth <= 0) return null
    return neighborhoodWithinDepth(edges, selectedNodeId, focusDepth)
  }, [edges, selectedNodeId, focusDepth])

  const selectedNode = selectedNodeId ? (nodesById.get(selectedNodeId) ?? null) : null
  const selectedNodeType = selectedNode ? nodeTypeById.get(selectedNode.node_type_id) : undefined
  const selectedResource = useMemo(
    () => resources.find((resource) => resource.node_id === selectedNodeId) ?? null,
    [resources, selectedNodeId],
  )
  const selectedWorkItem = useMemo(
    () => workItems.find((item) => item.node_id === selectedNodeId) ?? null,
    [workItems, selectedNodeId],
  )

  // Repository options for the Tasks workspace's repository selector (S8-F05): the
  // `github_repository` resources' stable projection node ids, never a free-text name.
  const repositories = useMemo(
    () =>
      resources
        .filter((resource) => resource.kind === 'github_repository')
        .map((resource) => ({ node_id: resource.node_id, title: resource.title })),
    [resources],
  )

  // The projected destination of the selected node for the Inspector's `Go to` action (S8-F01):
  // a Resource/WorkItem node routes to its owning tab, a generic node gets no destination.
  const goToTarget = useMemo(() => {
    if (selectedResource) {
      if (selectedResource.kind === 'github_repository') {
        return { view: 'repositories' as const, label: 'Repositories' }
      }
      if (selectedResource.kind === 'paper' || selectedResource.kind === 'article') {
        return { view: 'research' as const, label: 'Research' }
      }
      return null
    }
    if (selectedWorkItem) {
      return { view: 'tasks' as const, label: 'Tasks' }
    }
    return null
  }, [selectedResource, selectedWorkItem])

  const handleGoToProjected = useCallback(() => {
    if (!goToTarget) return
    if (goToTarget.view === 'tasks' && selectedWorkItem) {
      setSelectedWorkItemId(selectedWorkItem.id)
    }
    setActiveView(goToTarget.view)
  }, [goToTarget, selectedWorkItem])
  const nextWorkflowStep = selectedNodeType?.system_key
    ? NEXT_WORKFLOW_STEP[selectedNodeType.system_key]
    : undefined

  // Same-workspace, correct-semantic-type nodes the guided chain can connect the selected
  // node to instead of creating a duplicate (ST04-F07).
  const workflowChainCandidates = useMemo(() => {
    if (!nextWorkflowStep) return []
    const targetTypeIds = new Set(
      (workspace?.node_types ?? [])
        .filter((nodeType) => nodeType.system_key === nextWorkflowStep.targetSystemKey)
        .map((nodeType) => nodeType.id),
    )
    return visibleNodes
      .filter((node) => targetTypeIds.has(node.node_type_id))
      .map((node) => ({ id: node.id, title: node.title }))
  }, [nextWorkflowStep, workspace, visibleNodes])

  const relations = useMemo<RelationRow[]>(() => {
    if (!selectedNodeId) return []
    return edges
      .filter((edge) => edge.source_node_id === selectedNodeId || edge.target_node_id === selectedNodeId)
      .map((edge) => {
        const edgeType = edgeTypeById.get(edge.edge_type_id)
        const otherId = edge.source_node_id === selectedNodeId ? edge.target_node_id : edge.source_node_id
        const otherTitle = nodesById.get(otherId)?.title ?? 'Unknown'
        const isOutgoing = edge.source_node_id === selectedNodeId
        const label = isOutgoing
          ? (edgeType?.name ?? 'relates to')
          : (edgeType?.inverse_name ?? edgeType?.name ?? 'relates to')
        return { edgeTypeName: label, otherNodeTitle: otherTitle }
      })
  }, [edges, selectedNodeId, edgeTypeById, nodesById])

  const handlePlaceExisting = useCallback(
    async (nodeId: string) => {
      if (!activeCanvasId) return
      try {
        const { x, y } = randomSpawnPosition()
        const placement = await api.placeNode(activeCanvasId, nodeId, x, y)
        recordPersisted(placement)
        setPlacements((current) => [...current, placement])
        setSelectedNodeId(nodeId)
      } catch (error) {
        setActionError(`Could not place that object on this canvas: ${messageFor(error)}`)
      }
    },
    [activeCanvasId, recordPersisted],
  )

  // The Graph tab's typed create-node flow (S8-F03): with the TopBar global quick-capture gone,
  // every node type — including custom Schema-Editor types and generic graph-only nodes — is
  // instantiated here, landing on the active canvas like the old capture did.
  const handleCreateNode = useCallback(
    async (nodeTypeId: string, title: string) => {
      if (!workspace || !activeCanvasId) return
      try {
        const node = await api.captureNode(workspace.id, nodeTypeId, title)
        const { x, y } = randomSpawnPosition()
        const placement = await api.placeNode(activeCanvasId, node.id, x, y)
        recordPersisted(placement)
        setNodes((current) => [...current, node])
        setPlacements((current) => [...current, placement])
        setSelectedNodeId(node.id)
      } catch (error) {
        setActionError(`Could not create "${title}": ${messageFor(error)}`)
      }
    },
    [workspace, activeCanvasId, recordPersisted],
  )

  const handleMovePlacement = useCallback(
    (placementId: string, positionX: number, positionY: number) => {
      const seqByPlacement = placementMoveSeqRef.current
      const sequence = (seqByPlacement.get(placementId) ?? 0) + 1
      seqByPlacement.set(placementId, sequence)

      setPlacements((current) =>
        current.map((placement) =>
          placement.id === placementId
            ? { ...placement, position_x: positionX, position_y: positionY }
            : placement,
        ),
      )
      api
        .updatePlacement(placementId, { position_x: positionX, position_y: positionY })
        .then((updated) => {
          // A later move may already have superseded this one; only the still-latest
          // request's success should be recorded as the confirmed-persisted position.
          if (seqByPlacement.get(placementId) === sequence) recordPersisted(updated)
        })
        .catch((error) => {
          // A newer move for this placement has already been issued (or has already
          // succeeded); its own outcome owns the current position, so this older,
          // now-superseded failure must not touch anything.
          if (seqByPlacement.get(placementId) !== sequence) return
          setActionError(`Could not save the new position: ${messageFor(error)}`)
          // Revert to the last position actually known to have persisted — never to a
          // merely-prior optimistic value, which may itself never have been saved (e.g.
          // two overlapping moves that both fail).
          const revertTo = lastPersistedPositionRef.current.get(placementId)
          if (!revertTo) return
          setPlacements((current) =>
            current.map((placement) =>
              placement.id === placementId
                ? { ...placement, position_x: revertTo.x, position_y: revertTo.y }
                : placement,
            ),
          )
        })
    },
    [recordPersisted],
  )

  const handleChangeStatus = useCallback(
    async (statusId: string) => {
      if (!selectedNodeId || !workspace) return
      try {
        const updated = await api.updateNode(selectedNodeId, { status_id: statusId })
        setNodes((current) => current.map((node) => (node.id === updated.id ? updated : node)))
        refreshActiveViewData(workspace.id, activeView)
      } catch (error) {
        setActionError(`Could not update status: ${messageFor(error)}`)
      }
    },
    [selectedNodeId, workspace, activeView, refreshActiveViewData],
  )

  const handleChangeBody = useCallback(
    async (body: string): Promise<boolean> => {
      if (!selectedNodeId) return false
      try {
        const updated = await api.updateNode(selectedNodeId, { body })
        setNodes((current) => current.map((node) => (node.id === updated.id ? updated : node)))
        return true
      } catch (error) {
        setActionError(`Could not save the description: ${messageFor(error)}`)
        return false
      }
    },
    [selectedNodeId],
  )

  const handleChangeField = useCallback(
    async (fieldDefinitionId: string, value: unknown): Promise<boolean> => {
      if (!selectedNodeId) return false
      try {
        const updated = await api.updateNode(selectedNodeId, {
          field_values: { [fieldDefinitionId]: value },
        })
        setNodes((current) => current.map((node) => (node.id === updated.id ? updated : node)))
        return true
      } catch (error) {
        setActionError(`Could not save that field: ${messageFor(error)}`)
        return false
      }
    },
    [selectedNodeId],
  )

  const handleSetRepositoryLabel = useCallback(
    async (resourceId: string, label: RepositoryLabel) => {
      try {
        const updated = await api.updateResource(resourceId, { repository_label: label })
        setResources((current) => current.map((r) => (r.id === updated.id ? updated : r)))
      } catch (error) {
        setActionError(`Could not set that repository's label: ${messageFor(error)}`)
      }
    },
    [],
  )

  // Research/Repositories' own typed create forms (ST-07): a create-or-reuse call identical to
  // the Research create path, but invoked directly by the owning tab instead of routed through
  // an import batch.
  const handleCreateResource = useCallback(
    async (title: string, rawSource: string, kind: ResourceKind): Promise<Resource> => {
      if (!workspace) throw new Error('No active workspace')
      const resource = await api.createOrReuseResource(workspace.id, title, rawSource, kind)
      setResources((current) =>
        current.some((existing) => existing.id === resource.id)
          ? current.map((existing) => (existing.id === resource.id ? resource : existing))
          : [...current, resource],
      )
      return resource
    },
    [workspace],
  )

  const handleCreateWorkItem = useCallback(
    async (input: api.CreateWorkItemInput): Promise<WorkItem> => {
      const workItem = await api.createWorkItem(input)
      setWorkItems((current) => [...current, workItem])
      setSelectedWorkItemId(workItem.id)
      return workItem
    },
    [],
  )

  const handleUpdateWorkItem = useCallback(
    async (workItemId: string, patch: api.UpdateWorkItemPatch): Promise<WorkItem> => {
      const updated = await api.updateWorkItem(workItemId, patch)
      setWorkItems((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      )
      return updated
    },
    [],
  )

  const handleEditWorkItemBody = useCallback(
    async (workItemId: string, body: string) => {
      const workItem = workItems.find((item) => item.id === workItemId)
      if (!workItem) return
      const updated = await api.updateNode(workItem.node_id, { body })
      setNodes((current) => current.map((node) => (node.id === updated.id ? updated : node)))
      setWorkItems((current) =>
        current.map((item) => (item.id === workItemId ? { ...item, body } : item)),
      )
    },
    [workItems],
  )

  const handleAddChecklistItem = useCallback(
    async (workItemId: string, label: string) => {
      return api.addChecklistItem(workItemId, label)
    },
    [],
  )

  const handleUpdateChecklistItem = useCallback(
    async (checklistItemId: string, patch: api.UpdateChecklistItemPatch) => {
      return api.updateChecklistItem(checklistItemId, patch)
    },
    [],
  )

  const handleRemoveChecklistItem = useCallback(
    async (checklistItemId: string) => {
      await api.removeChecklistItem(checklistItemId)
    },
    [],
  )

  const handleReorderChecklistItems = useCallback(
    async (workItemId: string, orderedIds: string[]) => {
      return api.reorderChecklistItems(workItemId, orderedIds)
    },
    [],
  )

  const handleAttachWorkItemDocument = useCallback(
    async (workItemId: string, documentId: string) => {
      await api.attachWorkItemWikiLink(workItemId, documentId)
    },
    [],
  )

  const handleDetachWorkItemDocument = useCallback(
    async (workItemId: string, documentId: string) => {
      await api.detachWorkItemWikiLink(workItemId, documentId)
    },
    [],
  )

  // A relation points at another domain's object by node id; jumping to its owning tab
  // (Research for papers/articles, Repositories for GitHub repos, Tasks for work items)
  // mirrors the design gate's "Go to" navigation for projected nodes. Generic nodes have no
  // owning tab, so selection still moves but the active view does not.
  const handleGoToRelation = useCallback((relation: RelatedNode) => {
    setSelectedNodeId(relation.node_id)
    if (relation.target_domain === 'resource') {
      if (relation.resource_kind === 'github_repository') setActiveView('repositories')
      else if (relation.resource_kind === 'paper' || relation.resource_kind === 'article') {
        setActiveView('research')
      }
    } else if (relation.target_domain === 'work_item') {
      setSelectedWorkItemId(relation.target_entity_id)
      setActiveView('tasks')
    }
  }, [])

  const handleUpdateResource = useCallback(
    async (patch: api.UpdateResourcePatch): Promise<boolean> => {
      const resource = resources.find((r) => r.node_id === selectedNodeId)
      if (!resource) return false
      try {
        const updated = await api.updateResource(resource.id, patch)
        setResources((current) => current.map((r) => (r.id === updated.id ? updated : r)))
        if (workspace) refreshActiveViewData(workspace.id, activeView)
        return true
      } catch (error) {
        setActionError(`Could not save that research change: ${messageFor(error)}`)
        return false
      }
    },
    [resources, selectedNodeId, workspace, activeView, refreshActiveViewData],
  )

  const handleAdvanceWorkflow = useCallback(
    async (step: WorkflowChainStep, input: WorkflowChainAdvanceInput) => {
      if (!workspace || !selectedNodeId) return
      const patch =
        'title' in input
          ? { title: input.title }
          : { existing_target_node_id: input.existingTargetNodeId }
      const { node, edge } = await api.advanceWorkflowChain(
        workspace.id,
        selectedNodeId,
        step,
        patch,
      )
      // A selected-existing target is already in `nodes`; re-adding it would duplicate the row.
      setNodes((current) => (current.some((n) => n.id === node.id) ? current : [...current, node]))
      setEdges((current) => [...current, edge])
      setSelectedNodeId(node.id)
      refreshActiveViewData(workspace.id, activeView)
    },
    [workspace, selectedNodeId, activeView, refreshActiveViewData],
  )

  const handleArchiveSelected = useCallback(async () => {
    if (!selectedNodeId) return
    try {
      await api.archiveNode(selectedNodeId)
      setNodes((current) => current.filter((node) => node.id !== selectedNodeId))
      setSelectedNodeId(null)
    } catch (error) {
      setActionError(`Could not archive that object: ${messageFor(error)}`)
    }
  }, [selectedNodeId])

  const handleCreateCanvas = useCallback(async () => {
    if (!workspace) return
    const name = window.prompt('Name the new canvas')?.trim()
    if (!name) return
    try {
      const canvas = await api.createCanvas(workspace.id, name)
      setCanvases((current) => [...current, canvas])
      setActiveCanvasId(canvas.id)
    } catch (error) {
      setActionError(`Could not create canvas "${name}": ${messageFor(error)}`)
    }
  }, [workspace])

  const handleRequestConnect = useCallback(
    (sourceNodeId: string, targetNodeId: string) => {
      const sourceTitle = nodesById.get(sourceNodeId)?.title ?? 'Unknown'
      const targetTitle = nodesById.get(targetNodeId)?.title ?? 'Unknown'
      setPendingConnection({ sourceNodeId, sourceTitle, targetNodeId, targetTitle })
    },
    [nodesById],
  )

  const handleConfirmConnect = useCallback(
    async (edgeTypeId: string) => {
      if (!workspace || !pendingConnection) return
      try {
        const edge = await api.connectEdge(
          workspace.id,
          edgeTypeId,
          pendingConnection.sourceNodeId,
          pendingConnection.targetNodeId,
        )
        setEdges((current) => [...current, edge])
        setPendingConnection(null)
      } catch (error) {
        // Keep the pending connection open on failure: the user can retry without
        // rediscovering and redragging the two (still hard-to-hit) connection handles.
        setActionError(`Could not create that relationship: ${messageFor(error)}. You can try again.`)
      }
    },
    [workspace, pendingConnection],
  )

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null
      const isEditingText = Boolean(target && ['INPUT', 'SELECT', 'TEXTAREA'].includes(target.tagName))
      if (isEditingText) return

      if (event.key === 'Escape') {
        setSelectedNodeId(null)
        return
      }
      if ((event.key === 'Delete' || event.key === 'Backspace') && selectedNodeId) {
        event.preventDefault()
        handleArchiveSelected()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [selectedNodeId, handleArchiveSelected])

  if (!isUnlocked) {
    return <UnlockScreen onUnlocked={() => setIsUnlocked(true)} />
  }

  if (loadError) {
    return (
      <div className="app">
        <p className="inspector-empty">
          Could not reach the Personal Graph OS API: {loadError}. Is the backend running?
        </p>
      </div>
    )
  }

  return (
    <div className="app">
      <TopBar
        onOpenSchemaEditor={() => setIsSchemaEditorOpen(true)}
        onExportWorkspace={async () => {
          if (!workspace) return
          const blob = await api.exportWorkspace(workspace.id)
          const url = URL.createObjectURL(blob)
          const link = document.createElement('a')
          link.href = url
          link.download = `${workspace.id}.pgos-export.zip`
          link.click()
          URL.revokeObjectURL(url)
        }}
      />

      {actionError && (
        <div className="action-error" role="alert">
          <span>{actionError}</span>
          <button type="button" onClick={() => setActionError(null)} aria-label="Dismiss">
            ×
          </button>
        </div>
      )}

      <NavTabs activeView={activeView} onSelectView={setActiveView} />

      {/* Tasks, Activity, and Wiki own their full-page layouts (tree + editor + inspector), so
          the generic Inspector/Research/Workflow panel would otherwise keep showing whatever
          was last selected on a different tab — a stale, unrelated side panel with no
          connection to what's actually on screen. */}
      <div
        className={
          activeView === 'graph'
            ? 'workbench'
            : activeView === 'tasks' || activeView === 'activity' || activeView === 'wiki'
              ? 'workbench workbench-full'
              : 'workbench workbench-no-rail'
        }
      >
        {activeView === 'graph' && (
          <CanvasRail
            canvases={canvases}
            activeCanvasId={activeCanvasId}
            onSelectCanvas={setActiveCanvasId}
            onCreateCanvas={handleCreateCanvas}
          />
        )}

        {activeView === 'graph' && (
          <div className="stage-frame">
            <div className="stage-toolbar">
              <span className="stage-pill">
                {activeCanvas ? activeCanvas.name : '—'} · {visibleNodes.length} objects ·{' '}
                {edges.length} relations
              </span>
              <CreateNodeControl
                nodeTypes={workspace?.node_types ?? []}
                isDisabled={!activeCanvasId}
                onCreate={handleCreateNode}
              />
              <PlaceExistingNodeControl unplacedNodes={unplacedNodes} onPlace={handlePlaceExisting} />
            </div>

            <GraphCanvas
              canvas={activeCanvas}
              nodes={visibleNodes}
              edges={edges}
              placements={placements}
              nodeTypeById={nodeTypeById}
              edgeTypeById={edgeTypeById}
              statusById={statusById}
              selectedNodeId={selectedNodeId}
              focusSet={focusSet}
              onSelectNode={setSelectedNodeId}
              onMovePlacement={handleMovePlacement}
              onRequestConnect={handleRequestConnect}
            />

            <div className="focus-control">
              <span>Neighborhood focus</span>
              <input
                type="range"
                aria-label="Neighborhood focus"
                min={0}
                max={MAX_FOCUS_DEPTH}
                step={1}
                value={focusDepth}
                onChange={(event) => setFocusDepth(Number(event.target.value))}
                disabled={!selectedNodeId}
              />
              <span className="focus-value">
                {focusDepth === 0 ? 'Off' : `${focusDepth} hop${focusDepth > 1 ? 's' : ''}`}
              </span>
            </div>

            <div className="legend">
              <div className="legend-row">
                <span className="legend-swatch" style={{ background: 'var(--teal)' }} />
                Task
              </div>
              <div className="legend-row">
                <span className="legend-swatch" style={{ background: 'var(--brass)' }} />
                Project
              </div>
              <div className="legend-row">
                <span className="keyhint">drag</span>move ·{' '}
                <span className="keyhint">connect</span>relate ·{' '}
                <span className="keyhint">del</span>archive · <span className="keyhint">esc</span>
                deselect
              </div>
            </div>
          </div>
        )}

        {activeView === 'search' && (
          <div className="view-frame">
            <SearchView
              onSearch={(query, scope, offset) =>
                workspace
                  ? api.search(workspace.id, query, 20, false, scope, offset)
                  : Promise.resolve({ results: [], total: 0, has_more: false, offset: 0 })
              }
              selectedNodeId={selectedNodeId}
              onSelectNode={setSelectedNodeId}
            />
          </div>
        )}

        {activeView === 'tasks' && workspace && (
          <div className="view-frame">
            <TasksView
              workspaceId={workspace.id}
              workItems={workItems}
              documents={documents}
              repositories={repositories}
              selectedWorkItemId={selectedWorkItemId}
              onSelectWorkItem={setSelectedWorkItemId}
              onCreateWorkItem={handleCreateWorkItem}
              onUpdateWorkItem={handleUpdateWorkItem}
              onLoadDetail={api.getWorkItemDetail}
              onEditBody={handleEditWorkItemBody}
              onAddChecklistItem={handleAddChecklistItem}
              onUpdateChecklistItem={handleUpdateChecklistItem}
              onRemoveChecklistItem={handleRemoveChecklistItem}
              onReorderChecklistItems={handleReorderChecklistItems}
              onAttachDocument={handleAttachWorkItemDocument}
              onDetachDocument={handleDetachWorkItemDocument}
            />
          </div>
        )}

        {activeView === 'wiki' && workspace && (
          <div className="view-frame">
            <WikiView
              workspaceId={workspace.id}
              onLoadDocuments={(collectionId) => api.listDocuments(workspace.id, { collectionId })}
              onLoadDetail={(documentId) => api.getDocumentDetail(documentId)}
              onLoadVersions={(documentId) => api.listDocumentVersions(documentId)}
              onCreateDocument={(input) =>
                api.createDocument({ ...input, workspace_id: workspace.id })
              }
              onUpdateMetadata={(documentId, patch) => api.updateDocumentMetadata(documentId, patch)}
              onEditBody={(documentId, bodyMarkdown) =>
                api.editDocumentBody(documentId, bodyMarkdown, 'human/local-user/rest')
              }
              onLoadCollections={() => api.listCollections(workspace.id)}
              onCreateCollection={(name) => api.createCollection(workspace.id, name)}
              onLoadTags={() => api.listTags(workspace.id)}
              onAddDocumentLink={(documentId, targetDocumentId) =>
                api.addDocumentLink(documentId, 'document', targetDocumentId)
              }
            />
          </div>
        )}

        {activeView === 'research' && (
          <div className="view-frame">
            <ResearchView
              dashboard={researchDashboard}
              resources={resources}
              selectedNodeId={selectedNodeId}
              onSelectNode={setSelectedNodeId}
              onCreateResource={handleCreateResource}
            />
          </div>
        )}

        {activeView === 'repositories' && (
          <div className="view-frame">
            <RepositoriesView
              resources={resources}
              selectedNodeId={selectedNodeId}
              onSelectNode={setSelectedNodeId}
              onSetLabel={handleSetRepositoryLabel}
              onCreateResource={(title, rawSource) =>
                handleCreateResource(title, rawSource, 'github_repository')
              }
            />
          </div>
        )}

        {activeView === 'activity' && (
          <div className="view-frame">
            <ActivityView
              onLoadPage={(cursor) =>
                workspace
                  ? api.listActivityEvents(workspace.id, 50, cursor)
                  : Promise.resolve({ events: [], next_cursor: null })
              }
              onLoadDetail={(eventId) => {
                if (!workspace) return Promise.reject(new Error('No active workspace'))
                return api.getActivityEvent(workspace.id, eventId)
              }}
              onUndo={async (eventId, reason) => {
                if (!workspace) return
                await api.undoActivityEvent(workspace.id, eventId, reason)
                await reloadGraphData(workspace.id, activeCanvasId)
              }}
            />
          </div>
        )}

        {activeView !== 'tasks' && activeView !== 'activity' && activeView !== 'wiki' && (
          <div className="inspector-column">
            <Inspector
              node={selectedNode}
              nodeType={selectedNodeType}
              relations={relations}
              referenceableNodes={referenceableNodes}
              onChangeStatus={handleChangeStatus}
              onChangeBody={handleChangeBody}
              onChangeField={handleChangeField}
              onArchive={handleArchiveSelected}
              goToLabel={goToTarget?.label ?? null}
              onGoTo={handleGoToProjected}
            />
            {/* Research/Repositories own the rich canonical Resource detail (review finding
                S6-F01); every other view keeps only the generic Inspector above, even when a
                stale Resource selection carries over from a prior tab. */}
            {selectedResource && activeView === 'research' && (
              <ResearchDetailPanel
                key={selectedResource.id}
                resource={selectedResource}
                onUpdate={handleUpdateResource}
              />
            )}
            {selectedResource && (activeView === 'research' || activeView === 'repositories') && (
              <EnrichmentDetailPanel
                key={`enrichment-${selectedResource.id}`}
                resourceId={selectedResource.id}
                onLoadDetail={api.getResourceDetail}
                onGoToRelation={handleGoToRelation}
              />
            )}
            {nextWorkflowStep && (
              <WorkflowChainPanel
                nodeSystemKey={selectedNodeType?.system_key}
                existingTargetCandidates={workflowChainCandidates}
                onAdvance={(input) => handleAdvanceWorkflow(nextWorkflowStep.step, input)}
              />
            )}
          </div>
        )}
      </div>

      {pendingConnection && (
        <ConnectEdgeModal
          pending={pendingConnection}
          edgeTypes={workspace?.edge_types ?? []}
          onConfirm={handleConfirmConnect}
          onCancel={() => setPendingConnection(null)}
        />
      )}

      {isSchemaEditorOpen && workspace && (
        <SchemaEditor
          workspace={workspace}
          onWorkspaceChange={setWorkspace}
          onError={setActionError}
          onClose={() => setIsSchemaEditorOpen(false)}
        />
      )}

      <p className="footer-note">Personal Graph OS — local-first, graph-first workspace.</p>
    </div>
  )
}

export default App
