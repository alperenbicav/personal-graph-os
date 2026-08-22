import { useCallback } from 'react'
import type { AppView } from '../components/NavTabs'
import type { GraphEdge, GraphNode, ResearchDashboard, Resource, Workspace } from '../types'
import * as api from '../api/client'
import type { RepositoryLabel, ResourceKind, WorkflowChainStep } from '../types'
import type { WorkflowChainAdvanceInput } from '../components/WorkflowChainPanel'
import { messageFor } from '../lib/errors'

interface ResourceHandlerContext {
  workspace: Workspace | null
  activeView: AppView
  selectedNodeId: string | null
  resources: Resource[]
  setResources: React.Dispatch<React.SetStateAction<Resource[]>>
  setNodes: React.Dispatch<React.SetStateAction<GraphNode[]>>
  setEdges: React.Dispatch<React.SetStateAction<GraphEdge[]>>
  setResearchDashboard: React.Dispatch<React.SetStateAction<ResearchDashboard | null>>
  setSelectedNodeId: React.Dispatch<React.SetStateAction<string | null>>
  setActionError: React.Dispatch<React.SetStateAction<string | null>>
  refreshActiveViewData: (workspaceId: string, view: AppView) => Promise<void>
}

/** Research-resource mutation handlers extracted from App (ST-10): every callback keeps its
 * exact prior state updates, error banners, and refresh behavior. */
export function useResourceHandlers({
  workspace,
  activeView,
  selectedNodeId,
  resources,
  setResources,
  setNodes,
  setEdges,
  setResearchDashboard,
  setSelectedNodeId,
  setActionError,
  refreshActiveViewData,
}: ResourceHandlerContext) {
  const handleSetRepositoryLabel = useCallback(
    async (resourceId: string, label: RepositoryLabel) => {
      try {
        const updated = await api.updateResource(resourceId, { repository_label: label })
        setResources((current) => current.map((r) => (r.id === updated.id ? updated : r)))
      } catch (error) {
        setActionError(`Could not set that repository's label: ${messageFor(error)}`)
      }
    },
    [setActionError, setResources],
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
    [workspace, setResources],
  )

  const handleArchiveResource = useCallback(
    async (resourceId: string): Promise<void> => {
      try {
        const archived = await api.archiveResource(resourceId)
        setResources((current) => current.map((r) => (r.id === archived.id ? archived : r)))
        if (workspace) await refreshActiveViewData(workspace.id, activeView)
      } catch (error) {
        setActionError(`Could not archive that research item: ${messageFor(error)}`)
      }
    },
    [workspace, activeView, refreshActiveViewData, setActionError, setResources],
  )

  const handleDeleteResource = useCallback(
    async (resourceId: string): Promise<void> => {
      try {
        await api.deleteResource(resourceId)
        setResources((current) => current.filter((r) => r.id !== resourceId))
        setResearchDashboard((current) =>
          current
            ? {
                ...current,
                inbox: current.inbox.filter((r) => r.id !== resourceId),
                continue_reading: current.continue_reading.filter((r) => r.id !== resourceId),
                stale: current.stale.filter((r) => r.id !== resourceId),
                needs_takeaway: current.needs_takeaway.filter((r) => r.id !== resourceId),
                unlinked: current.unlinked.filter((r) => r.id !== resourceId),
                applied: current.applied.filter((r) => r.id !== resourceId),
              }
            : null,
        )
        if (workspace) await refreshActiveViewData(workspace.id, activeView)
      } catch (error) {
        setActionError(`Could not delete that research item: ${messageFor(error)}`)
      }
    },
    [workspace, activeView, refreshActiveViewData, setActionError, setResources, setResearchDashboard],
  )

  const handleUpdateResource = useCallback(
    async (patch: api.UpdateResourcePatch): Promise<boolean> => {
      const resource = resources.find((r) => r.node_id === selectedNodeId)
      if (!resource) return false
      try {
        const updated = await api.updateResource(resource.id, patch)
        setResources((current) => current.map((r) => (r.id === updated.id ? updated : r)))
        if (workspace) void refreshActiveViewData(workspace.id, activeView)
        return true
      } catch (error) {
        setActionError(`Could not save that research change: ${messageFor(error)}`)
        return false
      }
    },
    [resources, selectedNodeId, workspace, activeView, refreshActiveViewData, setActionError, setResources],
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
      void refreshActiveViewData(workspace.id, activeView)
    },
    [
      workspace,
      selectedNodeId,
      activeView,
      refreshActiveViewData,
      setNodes,
      setEdges,
      setSelectedNodeId,
    ],
  )

  return {
    handleSetRepositoryLabel,
    handleCreateResource,
    handleArchiveResource,
    handleDeleteResource,
    handleUpdateResource,
    handleAdvanceWorkflow,
  }
}
