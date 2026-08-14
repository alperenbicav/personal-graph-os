-- ST-10 (EP-2026-012): ClickUp channel ingress.
-- 1) `ingestion_jobs.external_url` carries the imported task's canonical ClickUp deep link as
--    channel metadata on the job (like `source_identifier`), purely additive.
-- 2) `channel_sync_state` records one monotonic per-channel cursor per workspace so a later
--    bidirectional or incremental sync can resume where the last successful import stopped;
--    today only `clickup` writes it, keyed by the imported task's `date_updated`.
ALTER TABLE ingestion_jobs ADD COLUMN external_url TEXT;

CREATE TABLE channel_sync_state (
    workspace_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    cursor_value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, channel)
);
