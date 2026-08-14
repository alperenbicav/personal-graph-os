-- ST-09 (EP-2026-012): work items get the same soft-archive flag documents already carry, so
-- archive is the default, recoverable lifecycle for every domain (the backing Node is NOT
-- archived — a work item's node stays visible on the graph; only the work item row is hidden
-- from the Tasks list). Purely additive; existing rows default to not archived.
ALTER TABLE work_items ADD COLUMN is_archived INTEGER NOT NULL DEFAULT 0;

CREATE INDEX idx_work_items_workspace_archived
    ON work_items (workspace_id, is_archived, created_at);
