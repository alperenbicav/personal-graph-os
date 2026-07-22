-- ST04-F01 refactor: Resource progress (0-100, nullable) so unfinished research can resurface
-- with how far a user actually got, not just its lifecycle status. Additive only — a database
-- already at 0002 upgrades in place; a fresh database applies 0001-0003 in order.
ALTER TABLE resources ADD COLUMN progress_percent INTEGER;
