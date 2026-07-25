-- ST-05 (EP-2026-012), review finding S5-R04: `IdempotencyReceipt` cannot record a plan_work
-- outcome because it is already uniquely claimed per (workspace_id, source, actor_name,
-- request_id) by CaptureService's own capture-submit receipt for the same request. Planning
-- idempotency is keyed on the captured IngestionJob itself instead -- the identity a capture
-- replay always resolves to the same value for -- so at most one plan outcome is ever recorded
-- per job, and a retried plan_work request reuses it instead of invoking the provider again or
-- creating a second Epic/Story/Task tree.
--
-- `status` records the terminal decision itself, not only a successful one: a classifier's
-- "do not plan" answer is receipted the same way a hierarchy is, so a retried job never
-- re-invokes the provider regardless of which terminal outcome it first reached. The three
-- hierarchy/document columns are therefore nullable and only ever populated together, enforced
-- by `WorkPlanningReceipt`'s own domain validator, not by a table-level constraint that a
-- single-connection SQLite CHECK could otherwise duplicate.
CREATE TABLE work_planning_receipts (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    ingestion_job_id TEXT NOT NULL REFERENCES ingestion_jobs (id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('planned', 'not_justified')),
    epic_work_item_id TEXT REFERENCES work_items (id) ON DELETE CASCADE,
    plan_document_id TEXT REFERENCES documents (id) ON DELETE CASCADE,
    plan_document_version_id TEXT,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_work_planning_receipts_job
    ON work_planning_receipts (ingestion_job_id);
