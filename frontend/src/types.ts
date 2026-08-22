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

// The Repositories workspace's ownership grouping (EP-2026-012 ST-06): every
// `github_repository` resource is exactly one of these.
export type RepositoryLabel = 'personal' | 'apilex' | 'liked_external'

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
  repository_label: RepositoryLabel | null
  title: string
  body: string
}

export interface ResourceListFilters {
  kind?: ResourceKind
  lifecycle_status?: ResourceLifecycleStatus
  repository_label?: RepositoryLabel
  last_activity_since?: string
  last_activity_until?: string
}

// None of these payload shapes carries its own discriminant on the wire — the parent
// `ResourceEnrichmentProfileVersion.resource_kind` says which one a given payload is.
export interface PaperEnrichmentPayload {
  summary: string
  key_findings: string[]
  methodology: string | null
  limitations: string[]
  applicability: string | null
  research_problem?: string | null
  motivation?: string | null
  related_work?: string | null
  datasets_and_experiments?: string | null
  key_results?: string[]
  comparisons_and_ablations?: string | null
  theoretical_contributions?: string | null
  practical_contributions?: string | null
  assumptions?: string | null
  open_questions?: string[]
  reproducibility?: string | null
  key_terms?: string[]
  figure_table_notes?: string | null
  interpretation?: string | null
  critical_analysis?: string | null
}

export interface ArticleEnrichmentPayload {
  summary: string
  key_findings: string[]
  applicability: string | null
  research_problem?: string | null
  motivation?: string | null
  key_results?: string[]
  practical_contributions?: string | null
  open_questions?: string[]
  critical_analysis?: string | null
}

export interface RepositoryEnrichmentPayload {
  summary: string
  capabilities: string[]
  architecture_summary: string | null
  risks: string[]
  applicability: string | null
  tech_stack: string[]
  license_name: string | null
  activity_summary: string | null
}

export type EnrichmentPayload =
  | PaperEnrichmentPayload
  | ArticleEnrichmentPayload
  | RepositoryEnrichmentPayload

export interface CitedEvidenceReference {
  adapter_name: string
  source_reference: string
  content_hash: string
  retrieved_at: string
}

export interface ResourceEnrichmentProfileVersion {
  id: string
  profile_id: string
  version_number: number
  resource_kind: ResourceKind
  payload: EnrichmentPayload
  tags: string[]
  evidence_content_hashes: string[]
  cited_evidence: CitedEvidenceReference[]
  authors: string[]
  published_at: string | null
  abstract: string | null
  provider_name: string
  model_name: string | null
  confidence: number
  created_by: string
  created_at: string
}

export type RelatedTargetDomain = 'resource' | 'work_item' | 'node'

export interface RelatedNode {
  edge_id: string
  direction: string
  edge_type_name: string
  node_id: string
  node_title: string
  target_domain: RelatedTargetDomain
  target_entity_id: string | null
  resource_kind: ResourceKind | null
}

export interface RelatedDocument {
  document_id: string
  title: string
  kind: string
}

export interface ResourceProvenance {
  ingestion_job_id: string
  source: string
  stage: string
  status: string
  created_at: string
}

export interface ResourceDetail {
  resource: Resource
  enrichment: ResourceEnrichmentProfileVersion | null
  relations: RelatedNode[]
  related_documents: RelatedDocument[]
  provenance: ResourceProvenance | null
}

export type DocumentKind = 'note' | 'lesson' | 'documentation' | 'plan'

export type DocumentLinkTargetType = 'node' | 'document'

export type WorkItemKind = 'epic' | 'story' | 'task'

export type WorkItemType = 'feature' | 'fix' | 'refactor' | 'research' | 'ops' | 'docs'

export type WorkItemStatus =
  | 'backlog'
  | 'planned'
  | 'in_progress'
  | 'in_review'
  | 'done'
  | 'production'
  | 'blocked'
  | 'cancelled'

export type WorkItemPriority = 'low' | 'medium' | 'high' | 'critical'

export interface WorkItem {
  id: string
  workspace_id: string
  node_id: string
  kind: WorkItemKind
  work_type: WorkItemType
  status: WorkItemStatus
  parent_id: string | null
  repository_node_id: string | null
  priority: WorkItemPriority | null
  due_date: string | null
  assignee: string | null
  blockers: string | null
  progress_percent: number | null
  source: string
  created_at: string
  updated_at: string
  title: string
  body: string
}

export interface WorkItemChecklistItem {
  id: string
  work_item_id: string
  position: number
  label: string
  is_completed: boolean
  created_at: string
}

export interface WorkItemDetail {
  work_item: WorkItem
  checklist_items: WorkItemChecklistItem[]
  linked_documents: DocumentLink[]
}

export interface WikiDocument {
  id: string
  workspace_id: string
  kind: DocumentKind
  title: string
  collection_id: string | null
  tag_ids: string[]
  is_archived: boolean
  source: string
  source_reference: string | null
  created_at: string
  updated_at: string
}

export interface DocumentVersion {
  id: string
  document_id: string
  version_number: number
  body_markdown: string
  created_by: string
  created_at: string
}

export interface DocumentLink {
  id: string
  document_id: string
  target_type: DocumentLinkTargetType
  target_id: string
  created_at: string
}

export interface Collection {
  id: string
  workspace_id: string
  name: string
  parent_id: string | null
  created_at: string
}

export interface Tag {
  id: string
  workspace_id: string
  name: string
  created_at: string
}

export interface DocumentDetail {
  document: WikiDocument
  latest_version: DocumentVersion
  collection: Collection | null
  tags: Tag[]
  outbound_links: DocumentLink[]
  backlinks: WikiDocument[]
  version_count: number
}

export type SearchScope = 'all' | 'wiki' | 'tasks' | 'research' | 'repositories' | 'graph'

export interface SearchResult {
  node: GraphNode | null
  resource: Resource | null
  document: WikiDocument | null
  snippet: string
  score: number
  scope: SearchScope
  entity_type: string
  source: string | null
  goto: string | null
}

export interface SearchResponse {
  results: SearchResult[]
  total: number
  has_more: boolean
  offset: number
}

export interface ResearchDashboard {
  inbox: Resource[]
  continue_reading: Resource[]
  stale: Resource[]
  needs_takeaway: Resource[]
  unlinked: Resource[]
  applied: Resource[]
}

export type WorkflowChainStep =
  | 'resource_to_takeaway'
  | 'takeaway_to_decision'
  | 'decision_to_task'
  | 'task_to_implementation'

export interface Attachment {
  id: string
  node_id: string
  file_name: string
  mime_type: string
  size_bytes: number
  checksum_sha256: string
  storage_relative_path: string
  created_at: string
}

export interface FileReference {
  id: string
  node_id: string
  machine_name: string
  relative_path: string
  repository_name: string | null
  absolute_path: string | null
  git_ref: string | null
  last_verified_at: string | null
  is_missing: boolean
}

export interface ActivityEventSummary {
  id: string
  workspace_id: string
  actor_kind: 'human' | 'agent'
  actor_name: string
  source: string
  entity_type: string
  entity_id: string
  action: 'created' | 'updated' | 'archived' | 'restored' | 'deleted'
  reason: string | null
  is_undoable: boolean
  occurred_at: string
  reverses_event_id: string | null
  disabled_reason:
    | 'snapshot_omitted_oversized'
    | 'unsupported_action'
    | 'already_reversed'
    | 'compensating_event'
    | null
}

export interface ActivityEvent extends ActivityEventSummary {
  session_id: string | null
  before_state: Record<string, unknown> | null
  after_state: Record<string, unknown> | null
  request_id: string | null
}

export interface ActivityEventPage {
  events: ActivityEventSummary[]
  next_cursor: string | null
}
