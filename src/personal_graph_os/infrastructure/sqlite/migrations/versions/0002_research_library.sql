-- ST-04: semantic schema roles, research-library fields, workspace research settings, and
-- full-text search. Migrations are append-only once applied (0001 is never edited), so this
-- upgrades an existing database in place rather than recreating tables that have no schema
-- change.

-- Immutable semantic roles a default node/edge type carries so the Resource -> Takeaway ->
-- Decision -> Task -> Implementation workflow can find it even after a user rename. NULL for
-- every user-created type. Uniqueness is enforced only among non-NULL values via a partial
-- index, since SQLite treats every NULL as distinct under a plain UNIQUE constraint anyway.
ALTER TABLE node_types ADD COLUMN system_key TEXT;
CREATE UNIQUE INDEX idx_node_types_system_key
    ON node_types (workspace_id, system_key)
    WHERE system_key IS NOT NULL;

ALTER TABLE edge_types ADD COLUMN system_key TEXT;
CREATE UNIQUE INDEX idx_edge_types_system_key
    ON edge_types (workspace_id, system_key)
    WHERE system_key IS NOT NULL;

-- Explicit, persisted acknowledgement that pausing a resource without a next action was a
-- deliberate choice, not an omission (see `Resource.next_action_dismissed`).
ALTER TABLE resources ADD COLUMN next_action_dismissed INTEGER NOT NULL DEFAULT 0;

-- Canonical identity must be unique per workspace independent of `kind` (decision #5): the
-- same URL/DOI/arXiv/GitHub identity is one resource regardless of how it was categorized.
-- SQLite cannot drop a UNIQUE constraint in place, so the table is recreated with the
-- corrected constraint and its rows are copied across unchanged (id-preserving).
CREATE TABLE resources_new (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    node_id TEXT NOT NULL UNIQUE REFERENCES nodes (id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    canonical_identifier TEXT NOT NULL,
    source_url TEXT,
    lifecycle_status TEXT NOT NULL DEFAULT 'inbox',
    next_action TEXT,
    next_action_dismissed INTEGER NOT NULL DEFAULT 0,
    open_questions_json TEXT NOT NULL DEFAULT '[]',
    takeaways_json TEXT NOT NULL DEFAULT '[]',
    review_at TEXT,
    last_activity_at TEXT NOT NULL,
    UNIQUE (workspace_id, canonical_identifier)
);

INSERT INTO resources_new (
    id, workspace_id, node_id, kind, canonical_identifier, source_url, lifecycle_status,
    next_action, next_action_dismissed, open_questions_json, takeaways_json, review_at,
    last_activity_at
)
SELECT
    id, workspace_id, node_id, kind, canonical_identifier, source_url, lifecycle_status,
    next_action, next_action_dismissed, open_questions_json, takeaways_json, review_at,
    last_activity_at
FROM resources;

DROP TABLE resources;
ALTER TABLE resources_new RENAME TO resources;

CREATE INDEX idx_resources_workspace_status ON resources (workspace_id, lifecycle_status);

-- Per-workspace research behavior settings. One row per workspace, created on first use with
-- the product-contract default (`stale_after_days = 14`); the application layer, not this
-- migration, is responsible for ensuring the row exists.
CREATE TABLE workspace_research_settings (
    workspace_id TEXT PRIMARY KEY REFERENCES workspaces (id) ON DELETE CASCADE,
    stale_after_days INTEGER NOT NULL DEFAULT 14
);

-- Full-text search over node title/body and Resource identity/kind/URL/takeaways/questions.
-- A contentless FTS5 table: the application layer (ST-04.3) owns keeping it in sync with the
-- canonical `nodes`/`resources` rows and compiling safe queries against it, so no indexing or
-- query logic lives in the schema itself.
CREATE VIRTUAL TABLE search_documents USING fts5(
    workspace_id UNINDEXED,
    entity_type UNINDEXED,
    entity_id UNINDEXED,
    text
);
