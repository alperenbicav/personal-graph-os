-- ST-01 (EP-2026-012): independent Markdown documents, collections/tags, optional document
-- links, resumable ingestion jobs, and the canonical Epic/Story/Task work hierarchy. Purely
-- additive -- no existing table is altered or recreated, so a database already at 0007 upgrades
-- in place and every pre-existing resource/node keeps rendering unchanged.

CREATE TABLE collections (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    parent_id TEXT REFERENCES collections (id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    UNIQUE (workspace_id, parent_id, name)
);

CREATE INDEX idx_collections_workspace ON collections (workspace_id);

CREATE TABLE tags (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (workspace_id, name)
);

CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    collection_id TEXT REFERENCES collections (id) ON DELETE SET NULL,
    is_archived INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_documents_workspace ON documents (workspace_id, is_archived);
CREATE INDEX idx_documents_collection ON documents (collection_id);

CREATE TABLE document_tags (
    document_id TEXT NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    tag_id TEXT NOT NULL REFERENCES tags (id) ON DELETE CASCADE,
    PRIMARY KEY (document_id, tag_id)
);

CREATE INDEX idx_document_tags_tag ON document_tags (tag_id);

-- Every edit is a new immutable version; the document row above never stores body text itself.
CREATE TABLE document_versions (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    body_markdown TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (document_id, version_number)
);

CREATE INDEX idx_document_versions_document ON document_versions (document_id, version_number);

-- Optional relation from a document to a graph node (the single canonical identity for every
-- graph-projected object, work items included) or another document. A document's existence and
-- validity never depends on any row existing here.
CREATE TABLE document_links (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (document_id, target_type, target_id)
);

CREATE INDEX idx_document_links_target ON document_links (target_type, target_id);

-- Resumable capture pipeline: one row per intake, tracked through received -> normalized ->
-- extracted -> enriched -> linked -> committed. `source`/`source_identifier` together are the
-- idempotent replay key a channel/capture adapter checks before creating a second job for the
-- same external item.
CREATE TABLE ingestion_jobs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    source_identifier TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT 'received',
    status TEXT NOT NULL DEFAULT 'pending',
    result_entity_type TEXT,
    result_entity_id TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, source, source_identifier)
);

CREATE INDEX idx_ingestion_jobs_workspace_status ON ingestion_jobs (workspace_id, status);

-- The canonical Epic/Story/Task hierarchy. `node_id` backs the same Node every other
-- graph-projected canonical object (e.g. `resources`) uses for title/body/projection.
-- `repository_node_id` is the associated repository's own stable projection node, never a
-- free-text name that a rename or duplicate could silently detach (review finding R01).
CREATE TABLE work_items (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    node_id TEXT NOT NULL UNIQUE REFERENCES nodes (id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    work_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'backlog',
    parent_id TEXT REFERENCES work_items (id) ON DELETE SET NULL,
    repository_node_id TEXT REFERENCES nodes (id) ON DELETE SET NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_work_items_workspace_status ON work_items (workspace_id, status);
CREATE INDEX idx_work_items_parent ON work_items (parent_id);
