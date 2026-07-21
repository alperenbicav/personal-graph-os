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
}

export interface NodeType {
  id: string
  name: string
  icon: string
  color_hex: string
  field_definitions: FieldDefinition[]
  status_definitions: StatusDefinition[]
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
