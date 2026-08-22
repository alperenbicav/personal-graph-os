import { useCallback } from 'react'
import type { GraphNode, WorkItem } from '../types'
import * as api from '../api/client'

interface WorkItemHandlerContext {
  workItems: WorkItem[]
  setWorkItems: React.Dispatch<React.SetStateAction<WorkItem[]>>
  setNodes: React.Dispatch<React.SetStateAction<GraphNode[]>>
  setSelectedWorkItemId: React.Dispatch<React.SetStateAction<string | null>>
}

/** Work-item mutation handlers extracted from App (ST-10): every callback keeps its exact
 * prior state updates and error-free fail-through semantics; the owning component still
 * renders errors via TasksView's own surfaces. */
export function useWorkItemHandlers({
  workItems,
  setWorkItems,
  setNodes,
  setSelectedWorkItemId,
}: WorkItemHandlerContext) {
  const handleCreateWorkItem = useCallback(
    async (input: api.CreateWorkItemInput): Promise<WorkItem> => {
      const workItem = await api.createWorkItem(input)
      setWorkItems((current) => [...current, workItem])
      setSelectedWorkItemId(workItem.id)
      return workItem
    },
    [setWorkItems, setSelectedWorkItemId],
  )

  const handleUpdateWorkItem = useCallback(
    async (workItemId: string, patch: api.UpdateWorkItemPatch): Promise<WorkItem> => {
      const updated = await api.updateWorkItem(workItemId, patch)
      setWorkItems((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      )
      return updated
    },
    [setWorkItems],
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
    [workItems, setNodes, setWorkItems],
  )

  const handleAddChecklistItem = useCallback(async (workItemId: string, label: string) => {
    return api.addChecklistItem(workItemId, label)
  }, [])

  const handleUpdateChecklistItem = useCallback(
    async (checklistItemId: string, patch: api.UpdateChecklistItemPatch) => {
      return api.updateChecklistItem(checklistItemId, patch)
    },
    [],
  )

  const handleRemoveChecklistItem = useCallback(async (checklistItemId: string) => {
    await api.removeChecklistItem(checklistItemId)
  }, [])

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

  return {
    handleCreateWorkItem,
    handleUpdateWorkItem,
    handleEditWorkItemBody,
    handleAddChecklistItem,
    handleUpdateChecklistItem,
    handleRemoveChecklistItem,
    handleReorderChecklistItems,
    handleAttachWorkItemDocument,
    handleDetachWorkItemDocument,
  }
}
