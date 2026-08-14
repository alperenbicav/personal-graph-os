-- ST-08 (EP-2026-012): the Tasks workspace's typed planning fields and checklist. Purely
-- additive -- `work_items` gains five nullable planning columns and a new child table
-- `work_item_checklist_items`. `progress_percent` mirrors `resources.progress_percent` (the
-- plan's "progress, 0-100, mirrors Resource.progress" decision, named to match that column);
-- the domain `WorkItem` validator is the single source of truth for legal values, matching the
-- existing `repository_label`/`progress_percent` conventions.
ALTER TABLE work_items ADD COLUMN priority TEXT;
ALTER TABLE work_items ADD COLUMN due_date TEXT;
ALTER TABLE work_items ADD COLUMN assignee TEXT;
ALTER TABLE work_items ADD COLUMN blockers TEXT;
ALTER TABLE work_items ADD COLUMN progress_percent INTEGER;

-- Checklist items are their own rows with stable ids so toggle/reorder/remove stay idempotent
-- and auditable (a JSON column would lose item identity); `position` is a dense 0-based order
-- the service rewrites on remove/reorder.
CREATE TABLE work_item_checklist_items (
    id TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL REFERENCES work_items (id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    label TEXT NOT NULL,
    is_completed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_work_item_checklist_items_work_item
    ON work_item_checklist_items (work_item_id, position);
