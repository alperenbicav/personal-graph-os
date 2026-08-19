-- ST-13: persisted, re-fetchable source content for one resource. Until now extraction's
-- bounded body text was discarded after enrichment (only its content hash survived as
-- evidence); the reading-first UI needs the full source text available without re-fetching
-- on every open. One row per resource, replaced wholesale by a refresh; the row is derived
-- evidence (reproducible from the source), so it cascades with its resource and carries the
-- content hash/adapter/retrieval provenance of exactly what was read.
CREATE TABLE resource_content (
    resource_id TEXT PRIMARY KEY REFERENCES resources (id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    body_markdown TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    adapter_name TEXT NOT NULL,
    retrieved_at TEXT NOT NULL
);

CREATE INDEX idx_resource_content_workspace ON resource_content (workspace_id);
