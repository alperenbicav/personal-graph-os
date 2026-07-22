// Mirrors the backend's domain models (personal_graph_os.domain.*). Kept intentionally
// close to the API's JSON shape rather than introducing a parallel modeling vocabulary.

export type FieldType =
  | 'text'
  | 'number'
  | 'boolean'
  | 'date'
  | 'select'
  | 'object_reference'
  | 'url'
  | 'file_path'

export interface FieldDefinition {
  id: string
  name: string
  field_type: FieldType
  is_required: boolean
  select_options: string[]
  description: string | null
}

export interface StatusDefinition {
  id: string
  name: string
  color_hex: string
  is_terminal: boolean
  sort_order: number
}

export interface EdgeType {
  id: string
  name: string
  inverse_name: string | null
  color_hex: string
  system_key?: string | null
}

export interface NodeType {
  id: string
  name: string
  icon: string
  color_hex: string
  field_definitions: FieldDefinition[]
  status_definitions: StatusDefinition[]
  system_key?: string | null
}

export interface Workspace {
  id: string
  name: string
  node_types: NodeType[]
  edge_types: EdgeType[]
  created_at: string
}

export interface GraphNode {
  id: string
  workspace_id: string
  node_type_id: string
  title: string
  body: string
  status_id: string | null
  field_values: Record<string, unknown>
  is_archived: boolean
  created_at: string
  updated_at: string
}

export interface GraphEdge {
  id: string
  workspace_id: string
  edge_type_id: string
  source_node_id: string
  target_node_id: string
  field_values: Record<string, unknown>
  created_at: string
}

export interface Canvas {
  id: string
  workspace_id: string
  name: string
  created_at: string
}

export interface CanvasPlacement {
  id: string
  canvas_id: string
  node_id: string
  position_x: number
  position_y: number
  width: number
  height: number
  is_collapsed: boolean
}

export type ResourceKind =
  | 'paper'
  | 'github_repository'
  | 'documentation'
  | 'specification'
  | 'article'
  | 'dataset'
  | 'video'
  | 'book'
  | 'other'

export type ResourceLifecycleStatus =
  | 'inbox'
  | 'to_review'
  | 'reading'
  | 'paused'
  | 'reviewed'
  | 'applied'
  | 'archived'

export interface Resource {
  id: string
  workspace_id: string
  node_id: string
  kind: ResourceKind
  canonical_identifier: string
  source_url: string | null
  lifecycle_status: ResourceLifecycleStatus
  next_action: string | null
  next_action_dismissed: boolean
  open_questions: string[]
  takeaways: string[]
  progress_percent: number | null
  review_at: string | null
  last_activity_at: string
  title: string
  body: string
}

export type ViewKind = 'table' | 'kanban' | 'timeline' | 'canvas' | 'search' | 'activity'

export type FilterField =
  | 'node_type_id'
  | 'status_id'
  | 'is_archived'
  | 'created_at'
  | 'updated_at'
  | 'resource_kind'
  | 'resource_lifecycle_status'
  | 'review_at'
  | 'last_activity_at'

export type FilterOperator = 'eq' | 'not_eq' | 'in' | 'before' | 'after' | 'is_null' | 'is_not_null'

export type SortDirection = 'asc' | 'desc'

export interface FilterClause {
  field: FilterField
  operator: FilterOperator
  value?: unknown
}

export interface SortClause {
  field: FilterField
  direction: SortDirection
}

export interface ProjectionQuery {
  filters?: FilterClause[]
  sort?: SortClause[]
  include_archived?: boolean
}

export interface SavedView {
  id: string
  workspace_id: string
  name: string
  view_kind: ViewKind
  filter_definition: Record<string, unknown>
  sort_definition: Record<string, unknown>
  created_at: string
}

export interface ProjectionItem {
  node: GraphNode
  resource: Resource | null
}

export interface SearchResult {
  node: GraphNode
  resource: Resource | null
  snippet: string
}

export interface ResearchDashboard {
  inbox: Resource[]
  continue_reading: Resource[]
  stale: Resource[]
  needs_takeaway: Resource[]
  unlinked: Resource[]
  applied: Resource[]
}

export interface WorkspaceResearchSettings {
  workspace_id: string
  stale_after_days: number
}

export type WorkflowChainStep =
  | 'resource_to_takeaway'
  | 'takeaway_to_decision'
  | 'decision_to_task'
  | 'task_to_implementation'

export type DiscoveryDecision = 'create' | 'reuse' | 'reject'

export interface DiscoveryCandidateInput {
  identifier: string
  title: string
  kind?: ResourceKind
  description?: string
  evidence?: string[]
}

export interface DiscoveryCandidatePreview {
  candidate: DiscoveryCandidateInput
  canonical_identifier: string | null
  decision: DiscoveryDecision
  reason: string
  existing_resource_id: string | null
  duplicate_of_candidate_index: number | null
}

export interface DiscoveryPreview {
  instruction: string
  sources_searched: string[]
  filters_interpreted: Record<string, unknown>
  candidates: DiscoveryCandidatePreview[]
}

export type DiscoveryOutcome = 'imported' | 'reused' | 'skipped' | 'failed'

export interface DiscoveredCandidate {
  raw_identifier: string
  canonical_identifier: string | null
  title: string
  kind: ResourceKind | null
  description: string
  evidence: string[]
  outcome: DiscoveryOutcome
  reason: string | null
  existing_resource_id: string | null
  imported_node_id: string | null
}

export interface DiscoveryRun {
  id: string
  workspace_id: string
  agent_identity: string
  instruction: string
  sources_searched: string[]
  filters_interpreted: Record<string, unknown>
  candidates: DiscoveredCandidate[]
  started_at: string
  completed_at: string | null
}
