-- ST-06 (EP-2026-012), review finding S6-R02 (delta round 2): `IngestionJobRepository
-- .get_by_result_entity()` filters by (workspace_id, result_entity_type, result_entity_id) and
-- orders by (created_at, id), but only an unrelated (workspace_id, status) index existed --
-- every Research/Repository detail read scanned the whole workspace's ingestion history before
-- sorting. This composite index covers the exact filter + order columns so the lookup stays
-- proportional to one resource's own capture count, not total channel history.
CREATE INDEX idx_ingestion_jobs_result_entity
    ON ingestion_jobs (workspace_id, result_entity_type, result_entity_id, created_at, id);
