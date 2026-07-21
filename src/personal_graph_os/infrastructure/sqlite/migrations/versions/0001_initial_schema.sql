-- Initial schema: schema definitions, canonical graph, canvas placements, projections,
-- files, audit trail, and the research library. Matches the domain model in
-- personal_graph_os.domain and WORK.md's canonical domain model section.

CREATE TABLE workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE node_types (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    icon TEXT NOT NULL DEFAULT 'circle',
    color_hex TEXT NOT NULL DEFAULT '#6b7280',
    UNIQUE (workspace_id, name)
);

CREATE TABLE field_definitions (
    id TEXT PRIMARY KEY,
    node_type_id TEXT NOT NULL REFERENCES node_types (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    field_type TEXT NOT NULL,
    is_required INTEGER NOT NULL DEFAULT 0,
    select_options_json TEXT NOT NULL DEFAULT '[]',
    description TEXT,
    UNIQUE (node_type_id, name)
);

CREATE TABLE status_definitions (
    id TEXT PRIMARY KEY,
    node_type_id TEXT NOT NULL REFERENCES node_types (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    color_hex TEXT NOT NULL DEFAULT '#6b7280',
    is_terminal INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE (node_type_id, name)
);

CREATE TABLE edge_types (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    inverse_name TEXT,
    color_hex TEXT NOT NULL DEFAULT '#6b7280',
    UNIQUE (workspace_id, name)
);

CREATE TABLE nodes (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    node_type_id TEXT NOT NULL REFERENCES node_types (id),
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    status_id TEXT REFERENCES status_definitions (id),
    field_values_json TEXT NOT NULL DEFAULT '{}',
    is_archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_nodes_workspace ON nodes (workspace_id, is_archived);

CREATE TABLE edges (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    edge_type_id TEXT NOT NULL REFERENCES edge_types (id),
    source_node_id TEXT NOT NULL REFERENCES nodes (id) ON DELETE CASCADE,
    target_node_id TEXT NOT NULL REFERENCES nodes (id) ON DELETE CASCADE,
    field_values_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX idx_edges_workspace ON edges (workspace_id);
CREATE INDEX idx_edges_source ON edges (source_node_id);
CREATE INDEX idx_edges_target ON edges (target_node_id);

CREATE TABLE canvases (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (workspace_id, name)
);

CREATE TABLE canvas_placements (
    id TEXT PRIMARY KEY,
    canvas_id TEXT NOT NULL REFERENCES canvases (id) ON DELETE CASCADE,
    node_id TEXT NOT NULL REFERENCES nodes (id) ON DELETE CASCADE,
    position_x REAL NOT NULL,
    position_y REAL NOT NULL,
    width REAL NOT NULL DEFAULT 240.0,
    height REAL NOT NULL DEFAULT 120.0,
    is_collapsed INTEGER NOT NULL DEFAULT 0,
    UNIQUE (canvas_id, node_id)
);

CREATE INDEX idx_canvas_placements_canvas ON canvas_placements (canvas_id);

CREATE TABLE saved_views (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    view_kind TEXT NOT NULL,
    filter_definition_json TEXT NOT NULL DEFAULT '{}',
    sort_definition_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE (workspace_id, name)
);

CREATE TABLE context_packs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    node_ids_json TEXT NOT NULL DEFAULT '[]',
    edge_ids_json TEXT NOT NULL DEFAULT '[]',
    evidence_pointers_json TEXT NOT NULL DEFAULT '[]',
    inclusion_reasons_json TEXT NOT NULL DEFAULT '{}',
    object_limit INTEGER NOT NULL DEFAULT 200,
    token_limit INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE attachments (
    id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL REFERENCES nodes (id) ON DELETE CASCADE,
    file_name TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    checksum_sha256 TEXT NOT NULL,
    storage_relative_path TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_attachments_node ON attachments (node_id);

CREATE TABLE file_references (
    id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL REFERENCES nodes (id) ON DELETE CASCADE,
    machine_name TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    repository_name TEXT,
    absolute_path TEXT,
    git_ref TEXT,
    last_verified_at TEXT,
    is_missing INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_file_references_node ON file_references (node_id);

CREATE TABLE activity_events (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    actor_kind TEXT NOT NULL,
    actor_name TEXT NOT NULL,
    source TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    action TEXT NOT NULL,
    session_id TEXT,
    reason TEXT,
    before_state_json TEXT,
    after_state_json TEXT,
    is_undoable INTEGER NOT NULL DEFAULT 0,
    occurred_at TEXT NOT NULL
);

CREATE INDEX idx_activity_events_workspace ON activity_events (workspace_id, occurred_at);
CREATE INDEX idx_activity_events_entity ON activity_events (entity_type, entity_id);

CREATE TABLE discovery_runs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    agent_identity TEXT NOT NULL,
    instruction TEXT NOT NULL,
    sources_searched_json TEXT NOT NULL DEFAULT '[]',
    filters_interpreted_json TEXT NOT NULL DEFAULT '{}',
    candidates_json TEXT NOT NULL DEFAULT '[]',
    started_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE resources (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    node_id TEXT NOT NULL UNIQUE REFERENCES nodes (id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    canonical_identifier TEXT NOT NULL,
    source_url TEXT,
    lifecycle_status TEXT NOT NULL DEFAULT 'inbox',
    next_action TEXT,
    open_questions_json TEXT NOT NULL DEFAULT '[]',
    takeaways_json TEXT NOT NULL DEFAULT '[]',
    review_at TEXT,
    last_activity_at TEXT NOT NULL,
    UNIQUE (workspace_id, kind, canonical_identifier)
);

CREATE INDEX idx_resources_workspace_status ON resources (workspace_id, lifecycle_status);
