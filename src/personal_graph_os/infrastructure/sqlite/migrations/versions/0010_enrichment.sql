-- ST-04 (EP-2026-012): agentic enrichment and relation engine. Purely additive -- no existing
-- table is altered, so a database already at 0009 upgrades in place.
--
-- `resource_enrichment_profiles` is keyed by (workspace_id, canonical_identifier), deliberately
-- not resource_id/node_id: a resource's enrichment history must survive its graph node being
-- deleted and recreated for the same captured source, so this table carries no foreign key to
-- `nodes` or `resources` at all. `current_version_id` is a soft pointer (no FK) to a row in
-- `resource_enrichment_profile_versions`, always set in the same transaction as the version it
-- points at, so it never dangles in practice; a hard FK here would require a circular table
-- dependency between the two tables.
CREATE TABLE resource_enrichment_profiles (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    canonical_identifier TEXT NOT NULL,
    resource_kind TEXT NOT NULL,
    current_version_number INTEGER NOT NULL DEFAULT 0,
    current_version_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, canonical_identifier)
);

-- One immutable, numbered enrichment result. `UNIQUE (profile_id, version_number)` is the
-- optimistic-concurrency guard: two concurrent writers computing the same next version_number
-- race on this constraint, and the loser's insert is translated into
-- `ConcurrentEnrichmentUpdateError` rather than silently overwriting the winner.
CREATE TABLE resource_enrichment_profile_versions (
    id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL REFERENCES resource_enrichment_profiles (id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    resource_kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    evidence_content_hashes_json TEXT NOT NULL,
    provider_name TEXT NOT NULL,
    model_name TEXT,
    confidence REAL NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (profile_id, version_number)
);

CREATE INDEX idx_enrichment_profile_versions_profile
    ON resource_enrichment_profile_versions (profile_id, version_number);

-- The persisted Review Inbox. Unlike the profile above, this is keyed to the *live* node: an
-- unresolved or auto-applied proposal about a node that no longer exists is not meaningful
-- history to retain, so both node references cascade/clear on node deletion.
CREATE TABLE relation_proposals (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    profile_version_id TEXT NOT NULL
        REFERENCES resource_enrichment_profile_versions (id) ON DELETE CASCADE,
    source_node_id TEXT NOT NULL REFERENCES nodes (id) ON DELETE CASCADE,
    relation_kind TEXT NOT NULL,
    candidate_label TEXT NOT NULL,
    confidence REAL NOT NULL,
    explanation TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'needs_review',
    resolved_target_node_id TEXT REFERENCES nodes (id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_relation_proposals_workspace_status ON relation_proposals (workspace_id, status);
