-- ST-06.2: an MCP mutation's idempotency key. `request_id` is nullable — a human/REST-
-- originated ActivityEvent never sets it. The partial unique index only applies to rows
-- that do set it, so replaying the same (workspace, source, actor, request) is detected
-- before a second mutation runs, without constraining any other event.
-- Additive only — a database already at 0004 upgrades in place.
ALTER TABLE activity_events ADD COLUMN request_id TEXT;

CREATE UNIQUE INDEX idx_activity_events_request_dedup
    ON activity_events (workspace_id, source, actor_name, request_id)
    WHERE request_id IS NOT NULL;
