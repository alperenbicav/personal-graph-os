import type {
  ActivityEvent,
  ActivityEventPage,
  Attachment,
  Canvas,
  CanvasPlacement,
  Collection,
  DiscoveryCandidateInput,
  DiscoveryPreview,
  DiscoveryRun,
  DocumentDetail,
  DocumentKind,
  DocumentLinkTargetType,
  DocumentVersion,
  EdgeType,
  FieldDefinition,
  FieldType,
  FileReference,
  GraphEdge,
  GraphNode,
  NodeType,
  ProjectionItem,
  ProjectionQuery,
  ResearchDashboard,
  Resource,
  ResourceDetail,
  ResourceKind,
  ResourceListFilters,
  SavedView,
  SearchResponse,
  SearchScope,
  StatusDefinition,
  Tag,
  ViewKind,
  WikiDocument,
  WorkflowChainStep,
  WorkItem,
  WorkItemChecklistItem,
  WorkItemDetail,
  WorkItemKind,
  WorkItemPriority,
  WorkItemStatus,
  WorkItemType,
  Workspace,
  WorkspaceResearchSettings,
} from '../types'
import { getToken, reportUnauthorized } from './session'

// Empty string resolves every request against the current page's own origin — the
// same-origin production posture ST-08.1 serves the UI under (`/app/` plus the API at
// root). `import.meta.env.DEV` gates the `VITE_API_BASE_URL` override to the Vite dev
// server only, so a leftover local `.env.local` can never bake a dev host into a
// production build (`vite build` sets `DEV` to `false` regardless of that file's contents).
const BASE_URL = import.meta.env.DEV ? (import.meta.env.VITE_API_BASE_URL ?? '') : ''

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

function authHeadersFromSession(): HeadersInit {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function throwIfUnauthorizedOrNotOk(response: Response): Promise<void> {
  if (response.ok) return
  if (response.status === 401) reportUnauthorized()
  const body = await response.json().catch(() => ({ detail: response.statusText }))
  throw new ApiError(response.status, body.detail ?? response.statusText)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...authHeadersFromSession(),
      ...init?.headers,
    },
  })
  await throwIfUnauthorizedOrNotOk(response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export function getWorkspace(): Promise<Workspace> {
  return request('/workspace')
}

export async function exportWorkspace(workspaceId: string): Promise<Blob> {
  const params = new URLSearchParams({ workspace_id: workspaceId })
  const response = await fetch(`${BASE_URL}/export?${params.toString()}`, {
    headers: authHeadersFromSession(),
  })
  await throwIfUnauthorizedOrNotOk(response)
  return response.blob()
}

export function listNodes(workspaceId: string): Promise<GraphNode[]> {
  return request(`/nodes?workspace_id=${encodeURIComponent(workspaceId)}`)
}

export function captureNode(
  workspaceId: string,
  nodeTypeId: string,
  title: string,
): Promise<GraphNode> {
  return request('/nodes', {
    method: 'POST',
    body: JSON.stringify({ workspace_id: workspaceId, node_type_id: nodeTypeId, title }),
  })
}

export interface UpdateNodePatch {
  title?: string
  body?: string
  status_id?: string
  field_values?: Record<string, unknown>
}

export function updateNode(nodeId: string, patch: UpdateNodePatch): Promise<GraphNode> {
  return request(`/nodes/${encodeURIComponent(nodeId)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
}

export function archiveNode(nodeId: string): Promise<GraphNode> {
  return request(`/nodes/${encodeURIComponent(nodeId)}`, { method: 'DELETE' })
}

export function listEdges(workspaceId: string): Promise<GraphEdge[]> {
  return request(`/edges?workspace_id=${encodeURIComponent(workspaceId)}`)
}

export function connectEdge(
  workspaceId: string,
  edgeTypeId: string,
  sourceNodeId: string,
  targetNodeId: string,
): Promise<GraphEdge> {
  return request('/edges', {
    method: 'POST',
    body: JSON.stringify({
      workspace_id: workspaceId,
      edge_type_id: edgeTypeId,
      source_node_id: sourceNodeId,
      target_node_id: targetNodeId,
    }),
  })
}

export function listCanvases(workspaceId: string): Promise<Canvas[]> {
  return request(`/canvases?workspace_id=${encodeURIComponent(workspaceId)}`)
}

export function createCanvas(workspaceId: string, name: string): Promise<Canvas> {
  return request('/canvases', {
    method: 'POST',
    body: JSON.stringify({ workspace_id: workspaceId, name }),
  })
}

export function listPlacements(canvasId: string): Promise<CanvasPlacement[]> {
  return request(`/canvases/${encodeURIComponent(canvasId)}/placements`)
}

export function placeNode(
  canvasId: string,
  nodeId: string,
  positionX: number,
  positionY: number,
): Promise<CanvasPlacement> {
  return request(`/canvases/${encodeURIComponent(canvasId)}/placements`, {
    method: 'POST',
    body: JSON.stringify({ node_id: nodeId, position_x: positionX, position_y: positionY }),
  })
}

export interface UpdatePlacementPatch {
  position_x?: number
  position_y?: number
  width?: number
  height?: number
  is_collapsed?: boolean
}

export function updatePlacement(
  placementId: string,
  patch: UpdatePlacementPatch,
): Promise<CanvasPlacement> {
  return request(`/placements/${encodeURIComponent(placementId)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
}

export function createNodeType(
  workspaceId: string,
  name: string,
  icon = 'circle',
  colorHex = '#6b7280',
): Promise<NodeType> {
  return request('/node-types', {
    method: 'POST',
    body: JSON.stringify({ workspace_id: workspaceId, name, icon, color_hex: colorHex }),
  })
}

export interface UpdateNodeTypePatch {
  name?: string
  icon?: string
  color_hex?: string
}

export function updateNodeType(
  workspaceId: string,
  nodeTypeId: string,
  patch: UpdateNodeTypePatch,
): Promise<NodeType> {
  return request(`/node-types/${encodeURIComponent(nodeTypeId)}`, {
    method: 'PATCH',
    body: JSON.stringify({ workspace_id: workspaceId, ...patch }),
  })
}

export interface CreateFieldDefinitionPatch {
  name: string
  field_type: FieldType
  is_required?: boolean
  select_options?: string[]
  description?: string | null
}

export function addFieldDefinition(
  workspaceId: string,
  nodeTypeId: string,
  patch: CreateFieldDefinitionPatch,
): Promise<FieldDefinition> {
  return request(`/node-types/${encodeURIComponent(nodeTypeId)}/fields`, {
    method: 'POST',
    body: JSON.stringify({ workspace_id: workspaceId, ...patch }),
  })
}

export function removeFieldDefinition(
  workspaceId: string,
  nodeTypeId: string,
  fieldDefinitionId: string,
): Promise<void> {
  return request(
    `/node-types/${encodeURIComponent(nodeTypeId)}/fields/${encodeURIComponent(fieldDefinitionId)}` +
      `?workspace_id=${encodeURIComponent(workspaceId)}`,
    { method: 'DELETE' },
  )
}

export interface UpdateFieldDefinitionPatch {
  name?: string
  field_type?: FieldType
  is_required?: boolean
  select_options?: string[]
  description?: string | null
  clear_description?: boolean
}

export function updateFieldDefinition(
  workspaceId: string,
  nodeTypeId: string,
  fieldDefinitionId: string,
  patch: UpdateFieldDefinitionPatch,
): Promise<FieldDefinition> {
  return request(
    `/node-types/${encodeURIComponent(nodeTypeId)}/fields/${encodeURIComponent(fieldDefinitionId)}`,
    { method: 'PATCH', body: JSON.stringify({ workspace_id: workspaceId, ...patch }) },
  )
}

export interface CreateStatusDefinitionPatch {
  name: string
  color_hex?: string
  is_terminal?: boolean
  sort_order?: number
}

export function addStatusDefinition(
  workspaceId: string,
  nodeTypeId: string,
  patch: CreateStatusDefinitionPatch,
): Promise<StatusDefinition> {
  return request(`/node-types/${encodeURIComponent(nodeTypeId)}/statuses`, {
    method: 'POST',
    body: JSON.stringify({ workspace_id: workspaceId, ...patch }),
  })
}

export function removeStatusDefinition(
  workspaceId: string,
  nodeTypeId: string,
  statusDefinitionId: string,
): Promise<void> {
  return request(
    `/node-types/${encodeURIComponent(nodeTypeId)}/statuses/${encodeURIComponent(statusDefinitionId)}` +
      `?workspace_id=${encodeURIComponent(workspaceId)}`,
    { method: 'DELETE' },
  )
}

export interface UpdateStatusDefinitionPatch {
  name?: string
  color_hex?: string
  is_terminal?: boolean
  sort_order?: number
}

export function updateStatusDefinition(
  workspaceId: string,
  nodeTypeId: string,
  statusDefinitionId: string,
  patch: UpdateStatusDefinitionPatch,
): Promise<StatusDefinition> {
  return request(
    `/node-types/${encodeURIComponent(nodeTypeId)}/statuses/${encodeURIComponent(statusDefinitionId)}`,
    { method: 'PATCH', body: JSON.stringify({ workspace_id: workspaceId, ...patch }) },
  )
}

export function createEdgeType(
  workspaceId: string,
  name: string,
  inverseName?: string,
  colorHex = '#6b7280',
): Promise<EdgeType> {
  return request('/edge-types', {
    method: 'POST',
    body: JSON.stringify({
      workspace_id: workspaceId,
      name,
      inverse_name: inverseName || null,
      color_hex: colorHex,
    }),
  })
}

export interface UpdateEdgeTypePatch {
  name?: string
  inverse_name?: string | null
  clear_inverse_name?: boolean
  color_hex?: string
}

export function updateEdgeType(
  workspaceId: string,
  edgeTypeId: string,
  patch: UpdateEdgeTypePatch,
): Promise<EdgeType> {
  return request(`/edge-types/${encodeURIComponent(edgeTypeId)}`, {
    method: 'PATCH',
    body: JSON.stringify({ workspace_id: workspaceId, ...patch }),
  })
}

export function removeEdgeType(workspaceId: string, edgeTypeId: string): Promise<void> {
  return request(
    `/edge-types/${encodeURIComponent(edgeTypeId)}?workspace_id=${encodeURIComponent(workspaceId)}`,
    { method: 'DELETE' },
  )
}

export function listResources(
  workspaceId: string,
  filters?: ResourceListFilters,
): Promise<Resource[]> {
  const params = new URLSearchParams({ workspace_id: workspaceId })
  if (filters?.kind) params.set('kind', filters.kind)
  if (filters?.lifecycle_status) params.set('lifecycle_status', filters.lifecycle_status)
  if (filters?.repository_label) params.set('repository_label', filters.repository_label)
  if (filters?.last_activity_since) params.set('last_activity_since', filters.last_activity_since)
  if (filters?.last_activity_until) params.set('last_activity_until', filters.last_activity_until)
  return request(`/resources?${params.toString()}`)
}

export function getResourceDetail(resourceId: string): Promise<ResourceDetail> {
  return request(`/resources/${encodeURIComponent(resourceId)}/detail`)
}

export function createOrReuseResource(
  workspaceId: string,
  title: string,
  rawSource: string,
  kind?: ResourceKind,
): Promise<Resource> {
  return request('/resources', {
    method: 'POST',
    body: JSON.stringify({ workspace_id: workspaceId, title, raw_source: rawSource, kind }),
  })
}

export interface UpdateResourcePatch {
  lifecycle_status?: string
  next_action?: string
  clear_next_action?: boolean
  next_action_dismissed?: boolean
  open_questions?: string[]
  takeaways?: string[]
  progress_percent?: number
  clear_progress_percent?: boolean
  review_at?: string | null
  clear_review_at?: boolean
  repository_label?: string
  clear_repository_label?: boolean
}

export function updateResource(resourceId: string, patch: UpdateResourcePatch): Promise<Resource> {
  return request(`/resources/${encodeURIComponent(resourceId)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
}

export function archiveResource(resourceId: string): Promise<Resource> {
  return request(`/resources/${encodeURIComponent(resourceId)}`, { method: 'DELETE' })
}

export function deleteResource(resourceId: string): Promise<void> {
  return request(`/resources/${encodeURIComponent(resourceId)}/hard?confirm_id=${encodeURIComponent(resourceId)}`, {
    method: 'DELETE',
  })
}

export function listDocuments(
  workspaceId: string,
  filters?: { collectionId?: string; includeArchived?: boolean },
): Promise<WikiDocument[]> {
  const params = new URLSearchParams({ workspace_id: workspaceId })
  if (filters?.collectionId) params.set('collection_id', filters.collectionId)
  if (filters?.includeArchived) params.set('include_archived', 'true')
  return request(`/documents?${params.toString()}`)
}

export function getDocument(documentId: string): Promise<WikiDocument> {
  return request(`/documents/${encodeURIComponent(documentId)}`)
}

export function getDocumentDetail(documentId: string): Promise<DocumentDetail> {
  return request(`/documents/${encodeURIComponent(documentId)}/detail`)
}

export function listDocumentVersions(documentId: string): Promise<DocumentVersion[]> {
  return request(`/documents/${encodeURIComponent(documentId)}/versions`)
}

export interface CreateDocumentInput {
  workspace_id: string
  title: string
  kind: DocumentKind
  source: string
  body_markdown?: string
  collection_id?: string | null
  tag_names?: string[]
}

export function createDocument(payload: CreateDocumentInput): Promise<WikiDocument> {
  return request('/documents', { method: 'POST', body: JSON.stringify(payload) })
}

export interface UpdateDocumentMetadataPatch {
  title?: string
  kind?: DocumentKind
  collection_id?: string | null
  clear_collection?: boolean
  tag_names?: string[]
  is_archived?: boolean
}

export function updateDocumentMetadata(
  documentId: string,
  patch: UpdateDocumentMetadataPatch,
): Promise<WikiDocument> {
  return request(`/documents/${encodeURIComponent(documentId)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
}

export function editDocumentBody(
  documentId: string,
  bodyMarkdown: string,
  actor: string,
): Promise<DocumentVersion> {
  return request(`/documents/${encodeURIComponent(documentId)}/versions`, {
    method: 'POST',
    body: JSON.stringify({ body_markdown: bodyMarkdown, actor }),
  })
}

export function addDocumentLink(
  documentId: string,
  targetType: DocumentLinkTargetType,
  targetId: string,
): Promise<void> {
  return request(`/documents/${encodeURIComponent(documentId)}/links`, {
    method: 'POST',
    body: JSON.stringify({ target_type: targetType, target_id: targetId }),
  })
}

export function removeDocumentLink(documentLinkId: string): Promise<void> {
  return request(`/document-links/${encodeURIComponent(documentLinkId)}`, { method: 'DELETE' })
}

export function listCollections(workspaceId: string): Promise<Collection[]> {
  return request(`/collections?workspace_id=${encodeURIComponent(workspaceId)}`)
}

export function createCollection(
  workspaceId: string,
  name: string,
  parentId?: string,
): Promise<Collection> {
  return request('/collections', {
    method: 'POST',
    body: JSON.stringify({ workspace_id: workspaceId, name, parent_id: parentId }),
  })
}

export function listTags(workspaceId: string): Promise<Tag[]> {
  return request(`/tags?workspace_id=${encodeURIComponent(workspaceId)}`)
}

export function createTag(workspaceId: string, name: string): Promise<Tag> {
  return request('/tags', { method: 'POST', body: JSON.stringify({ workspace_id: workspaceId, name }) })
}

export function listWorkItems(workspaceId: string): Promise<WorkItem[]> {
  return request(`/work-items?workspace_id=${encodeURIComponent(workspaceId)}`)
}

export function getWorkItem(workItemId: string): Promise<WorkItem> {
  return request(`/work-items/${encodeURIComponent(workItemId)}`)
}

export function getWorkItemDetail(workItemId: string): Promise<WorkItemDetail> {
  return request(`/work-items/${encodeURIComponent(workItemId)}/detail`)
}

export interface CreateWorkItemInput {
  workspace_id: string
  kind: WorkItemKind
  work_type: WorkItemType
  title: string
  body?: string
  source: string
  status?: WorkItemStatus
  parent_id?: string | null
  repository_node_id?: string | null
}

export function createWorkItem(payload: CreateWorkItemInput): Promise<WorkItem> {
  return request('/work-items', { method: 'POST', body: JSON.stringify(payload) })
}

export interface UpdateWorkItemPatch {
  work_type?: WorkItemType
  status?: WorkItemStatus
  priority?: WorkItemPriority
  due_date?: string | null
  assignee?: string | null
  blockers?: string | null
  progress_percent?: number
  repository_node_id?: string | null
  clear_priority?: boolean
  clear_due_date?: boolean
  clear_assignee?: boolean
  clear_blockers?: boolean
  clear_progress_percent?: boolean
  clear_repository_node_id?: boolean
}

export function updateWorkItem(
  workItemId: string,
  patch: UpdateWorkItemPatch,
): Promise<WorkItem> {
  return request(`/work-items/${encodeURIComponent(workItemId)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
}

export interface UpdateChecklistItemPatch {
  label?: string
  is_completed?: boolean
}

export function addChecklistItem(
  workItemId: string,
  label: string,
): Promise<WorkItemChecklistItem> {
  return request(`/work-items/${encodeURIComponent(workItemId)}/checklist-items`, {
    method: 'POST',
    body: JSON.stringify({ label }),
  })
}

export function updateChecklistItem(
  checklistItemId: string,
  patch: UpdateChecklistItemPatch,
): Promise<WorkItemChecklistItem> {
  return request(`/checklist-items/${encodeURIComponent(checklistItemId)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
}

export function removeChecklistItem(checklistItemId: string): Promise<void> {
  return request(`/checklist-items/${encodeURIComponent(checklistItemId)}`, {
    method: 'DELETE',
  })
}

export function reorderChecklistItems(
  workItemId: string,
  orderedIds: string[],
): Promise<WorkItemChecklistItem[]> {
  return request(`/work-items/${encodeURIComponent(workItemId)}/checklist-items/reorder`, {
    method: 'POST',
    body: JSON.stringify({ ordered_ids: orderedIds }),
  })
}

export function attachWorkItemWikiLink(
  workItemId: string,
  documentId: string,
): Promise<void> {
  return request(`/work-items/${encodeURIComponent(workItemId)}/wiki-links`, {
    method: 'POST',
    body: JSON.stringify({ document_id: documentId }),
  })
}

export function detachWorkItemWikiLink(
  workItemId: string,
  documentId: string,
): Promise<void> {
  return request(
    `/work-items/${encodeURIComponent(workItemId)}/wiki-links?document_id=${encodeURIComponent(documentId)}`,
    { method: 'DELETE' },
  )
}

export function search(
  workspaceId: string,
  query: string,
  limit = 20,
  includeArchived = false,
  scope: SearchScope = 'all',
  offset = 0,
): Promise<SearchResponse> {
  const params = new URLSearchParams({
    workspace_id: workspaceId,
    q: query,
    limit: String(limit),
    include_archived: String(includeArchived),
    scope,
    offset: String(offset),
  })
  return request(`/search?${params.toString()}`)
}

export function listActivityEvents(
  workspaceId: string,
  limit = 50,
  cursor?: string | null,
): Promise<ActivityEventPage> {
  const params = new URLSearchParams({ workspace_id: workspaceId, limit: String(limit) })
  if (cursor) params.set('cursor', cursor)
  return request(`/activity-events?${params.toString()}`)
}

export function getActivityEvent(workspaceId: string, eventId: string): Promise<ActivityEvent> {
  const params = new URLSearchParams({ workspace_id: workspaceId })
  return request(`/activity-events/${encodeURIComponent(eventId)}?${params.toString()}`)
}

export function undoActivityEvent(
  workspaceId: string,
  eventId: string,
  reason: string,
): Promise<ActivityEvent> {
  const params = new URLSearchParams({ workspace_id: workspaceId })
  return request(`/activity-events/${encodeURIComponent(eventId)}/undo?${params.toString()}`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  })
}

export function listSavedViews(workspaceId: string): Promise<SavedView[]> {
  return request(`/saved-views?workspace_id=${encodeURIComponent(workspaceId)}`)
}

export function createSavedView(
  workspaceId: string,
  name: string,
  viewKind: ViewKind,
  query: ProjectionQuery = {},
): Promise<SavedView> {
  return request('/saved-views', {
    method: 'POST',
    body: JSON.stringify({ workspace_id: workspaceId, name, view_kind: viewKind, query }),
  })
}

export function deleteSavedView(savedViewId: string): Promise<void> {
  return request(`/saved-views/${encodeURIComponent(savedViewId)}`, { method: 'DELETE' })
}

interface ViewRequestBase {
  workspace_id: string
  query?: ProjectionQuery
  saved_view_id?: string
}

export function evaluateTableView(payload: ViewRequestBase): Promise<ProjectionItem[]> {
  return request('/views/table', { method: 'POST', body: JSON.stringify(payload) })
}

export function evaluateKanbanView(
  payload: ViewRequestBase & { group_by: string },
): Promise<Record<string, ProjectionItem[]>> {
  return request('/views/kanban', { method: 'POST', body: JSON.stringify(payload) })
}

export function evaluateTimelineView(
  payload: ViewRequestBase & { date_field: string },
): Promise<ProjectionItem[]> {
  return request('/views/timeline', { method: 'POST', body: JSON.stringify(payload) })
}

export function getResearchDashboard(workspaceId: string): Promise<ResearchDashboard> {
  return request(`/research/dashboard?workspace_id=${encodeURIComponent(workspaceId)}`)
}

export function getResearchSettings(workspaceId: string): Promise<WorkspaceResearchSettings> {
  return request(`/research-settings?workspace_id=${encodeURIComponent(workspaceId)}`)
}

export function updateResearchSettings(
  workspaceId: string,
  staleAfterDays: number,
): Promise<WorkspaceResearchSettings> {
  return request(`/research-settings?workspace_id=${encodeURIComponent(workspaceId)}`, {
    method: 'PATCH',
    body: JSON.stringify({ stale_after_days: staleAfterDays }),
  })
}

export interface AdvanceWorkflowChainPatch {
  title?: string
  existing_target_node_id?: string
}

export function advanceWorkflowChain(
  workspaceId: string,
  sourceNodeId: string,
  step: WorkflowChainStep,
  patch: AdvanceWorkflowChainPatch,
): Promise<{ node: GraphNode; edge: GraphEdge }> {
  return request('/workflow-chain/advance', {
    method: 'POST',
    body: JSON.stringify({
      workspace_id: workspaceId,
      source_node_id: sourceNodeId,
      step,
      ...patch,
    }),
  })
}

export function previewDiscovery(
  workspaceId: string,
  instruction: string,
  candidates: DiscoveryCandidateInput[],
): Promise<DiscoveryPreview> {
  return request('/discovery/preview', {
    method: 'POST',
    body: JSON.stringify({ workspace_id: workspaceId, instruction, candidates }),
  })
}

export function applyDiscovery(
  workspaceId: string,
  agentIdentity: string,
  instruction: string,
  candidates: DiscoveryCandidateInput[],
): Promise<DiscoveryRun> {
  return request('/discovery/apply', {
    method: 'POST',
    body: JSON.stringify({
      workspace_id: workspaceId,
      agent_identity: agentIdentity,
      instruction,
      candidates,
    }),
  })
}

export function listAttachments(nodeId: string): Promise<Attachment[]> {
  return request(`/nodes/${encodeURIComponent(nodeId)}/attachments`)
}

// Bypasses `request()`: a multipart body must let the browser set its own
// `Content-Type` boundary, which `request()`'s fixed JSON header would override.
export async function uploadAttachment(nodeId: string, file: File): Promise<Attachment> {
  const formData = new FormData()
  formData.append('file', file)
  const response = await fetch(`${BASE_URL}/nodes/${encodeURIComponent(nodeId)}/attachments`, {
    method: 'POST',
    headers: authHeadersFromSession(),
    body: formData,
  })
  await throwIfUnauthorizedOrNotOk(response)
  return (await response.json()) as Attachment
}

// Bypasses `request()`: the response body is binary, not JSON, and a browser download
// needs the bearer token attached via `fetch` since a plain `<a href>` cannot set headers.
export async function downloadAttachment(attachmentId: string, fileName: string): Promise<void> {
  const response = await fetch(
    `${BASE_URL}/attachments/${encodeURIComponent(attachmentId)}/download`,
    { headers: authHeadersFromSession() },
  )
  await throwIfUnauthorizedOrNotOk(response)
  const blob = await response.blob()
  const objectUrl = URL.createObjectURL(blob)
  try {
    const anchor = document.createElement('a')
    anchor.href = objectUrl
    anchor.download = fileName
    anchor.click()
  } finally {
    URL.revokeObjectURL(objectUrl)
  }
}

export function deleteAttachment(attachmentId: string): Promise<void> {
  return request(`/attachments/${encodeURIComponent(attachmentId)}`, { method: 'DELETE' })
}

export function listFileReferences(nodeId: string): Promise<FileReference[]> {
  return request(`/nodes/${encodeURIComponent(nodeId)}/file-references`)
}

export interface CreateFileReferenceInput {
  machine_name: string
  relative_path: string
  repository_name?: string | null
  absolute_path?: string | null
  git_ref?: string | null
}

export function createFileReference(
  nodeId: string,
  input: CreateFileReferenceInput,
): Promise<FileReference> {
  return request(`/nodes/${encodeURIComponent(nodeId)}/file-references`, {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export function deleteFileReference(fileReferenceId: string): Promise<void> {
  return request(`/file-references/${encodeURIComponent(fileReferenceId)}`, { method: 'DELETE' })
}

export function verifyFileReference(fileReferenceId: string): Promise<FileReference> {
  return request(`/file-references/${encodeURIComponent(fileReferenceId)}/verify`, {
    method: 'POST',
  })
}
