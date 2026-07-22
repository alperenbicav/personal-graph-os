import type {
  Canvas,
  CanvasPlacement,
  DiscoveryCandidateInput,
  DiscoveryPreview,
  DiscoveryRun,
  EdgeType,
  FieldDefinition,
  FieldType,
  GraphEdge,
  GraphNode,
  NodeType,
  ProjectionItem,
  ProjectionQuery,
  ResearchDashboard,
  Resource,
  ResourceKind,
  SavedView,
  SearchResult,
  StatusDefinition,
  ViewKind,
  WorkflowChainStep,
  Workspace,
  WorkspaceResearchSettings,
} from '../types'

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

// The token is a local secret copied from the backend's console output into a
// git-ignored `.env.local` (see `.env.local.example`); it must never be hardcoded here
// or otherwise land in source or a committed build.
const API_TOKEN = import.meta.env.VITE_API_TOKEN

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(API_TOKEN ? { Authorization: `Bearer ${API_TOKEN}` } : {}),
      ...init?.headers,
    },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }))
    throw new ApiError(response.status, body.detail ?? response.statusText)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export function getWorkspace(): Promise<Workspace> {
  return request('/workspace')
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

export function listResources(workspaceId: string): Promise<Resource[]> {
  return request(`/resources?workspace_id=${encodeURIComponent(workspaceId)}`)
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

export function search(
  workspaceId: string,
  query: string,
  limit = 20,
  includeArchived = false,
): Promise<SearchResult[]> {
  const params = new URLSearchParams({
    workspace_id: workspaceId,
    q: query,
    limit: String(limit),
    include_archived: String(includeArchived),
  })
  return request(`/search?${params.toString()}`)
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
