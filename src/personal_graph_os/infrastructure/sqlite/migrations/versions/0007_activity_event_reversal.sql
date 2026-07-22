-- ST-07.1: append-only activity foundation. Adds a nullable self-reference so a compensating
-- (undo) event can point back at the event it reverses, without ever editing the original row.
-- `idx_activity_events_reverses_dedup` enforces at most one compensating event per reversed
-- event (undo is not repeatable); NULL values are not compared by a unique index, so ordinary
-- events (which never set this column) are unaffected. Additive only -- a database already at
-- 0006 upgrades in place; existing rows keep `reverses_event_id = NULL` and remain readable.
ALTER TABLE activity_events
    ADD COLUMN reverses_event_id TEXT REFERENCES activity_events (id);

CREATE UNIQUE INDEX idx_activity_events_reverses_dedup
    ON activity_events (reverses_event_id)
    WHERE reverses_event_id IS NOT NULL;

-- Bounded cursor-paginated reads order by `(occurred_at, id)` descending; this composite index
-- covers both the workspace-wide feed and keyset pagination without a separate sort step.
CREATE INDEX idx_activity_events_workspace_cursor
    ON activity_events (workspace_id, occurred_at DESC, id DESC);

CREATE INDEX idx_activity_events_entity_cursor
    ON activity_events (entity_type, entity_id, occurred_at DESC, id DESC);
