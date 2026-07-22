-- ST05-F01 refactor: a durable journal so a managed-file delete/upload compensation that
-- itself fails (store or repository I/O error) is retryable/reconcilable across a restart
-- instead of silently leaving an orphaned managed file or an undownloadable live row.
-- Additive only — a database already at 0003 upgrades in place.
CREATE TABLE pending_file_operations (
    id TEXT PRIMARY KEY,
    attachment_id TEXT NOT NULL,
    storage_relative_path TEXT NOT NULL,
    quarantine_token TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_pending_file_operations_attachment ON pending_file_operations (attachment_id);
