-- ST06-F01 refactor: a durable idempotency receipt separate from the `activity_events` audit
-- trail. The receipt binds (workspace, source, actor, request) to the exact operation and a
-- canonical payload fingerprint plus the shaped result to replay, so a reused request_id with a
-- different tool or payload is rejected instead of silently replaying an unrelated result, and a
-- no-op/reuse mutation still gets a durable, replayable receipt without inventing an
-- ActivityEvent. Additive only -- a database already at 0005 upgrades in place.
CREATE TABLE idempotency_receipts (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    actor_name TEXT NOT NULL,
    request_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    payload_fingerprint TEXT NOT NULL,
    result_payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_idempotency_receipts_request_dedup
    ON idempotency_receipts (workspace_id, source, actor_name, request_id);
