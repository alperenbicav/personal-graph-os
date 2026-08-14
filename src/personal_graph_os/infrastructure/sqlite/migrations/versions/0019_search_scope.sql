-- ST-12 (EP-2026-012): scoped search. FTS5 virtual tables cannot be `ALTER`ed, so this adds the
-- `scope` column by rebuilding `search_documents` with the new schema (CREATE + copy + DROP +
-- RENAME, a documented FTS5 pattern) and backfills every row's scope from the canonical tables it
-- indexes. Runtime indexers also write `scope` on every insert, so this backfill is one-time;
-- re-running startup `backfill_search_index` stays idempotent (delete-then-insert carries scope).
CREATE VIRTUAL TABLE search_documents_new USING fts5(
    workspace_id UNINDEXED,
    entity_type UNINDEXED,
    entity_id UNINDEXED,
    text,
    scope UNINDEXED
);

INSERT INTO search_documents_new (workspace_id, entity_type, entity_id, text, scope)
SELECT
    workspace_id,
    entity_type,
    entity_id,
    text,
    CASE
        WHEN entity_type = 'document' THEN 'wiki'
        WHEN entity_type = 'resource' THEN CASE
            WHEN (SELECT kind FROM resources WHERE node_id = search_documents.entity_id)
                 IN ('paper', 'article') THEN 'research'
            WHEN (SELECT kind FROM resources WHERE node_id = search_documents.entity_id)
                 = 'github_repository' THEN 'repositories'
            ELSE 'graph' END
        WHEN entity_type = 'node' THEN CASE
            WHEN EXISTS (SELECT 1 FROM work_items WHERE node_id = search_documents.entity_id)
                 THEN 'tasks'
            WHEN EXISTS (SELECT 1 FROM resources WHERE node_id = search_documents.entity_id)
                 THEN CASE
                     WHEN (SELECT kind FROM resources WHERE node_id = search_documents.entity_id)
                          IN ('paper', 'article') THEN 'research'
                     WHEN (SELECT kind FROM resources WHERE node_id = search_documents.entity_id)
                          = 'github_repository' THEN 'repositories'
                     ELSE 'graph' END
            ELSE 'graph' END
        ELSE 'graph' END
FROM search_documents;

DROP TABLE search_documents;
ALTER TABLE search_documents_new RENAME TO search_documents;
