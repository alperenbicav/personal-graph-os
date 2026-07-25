-- ST-06 (EP-2026-012): the Repositories workspace separates personal/Apilex/liked-external
-- repositories by label. Nullable and additive, mirroring the existing `kind`/`lifecycle_status`
-- columns (no table-level CHECK there either) -- the domain's own `Resource` validator is the
-- single source of truth for which values are legal and which kind may carry one at all.
ALTER TABLE resources ADD COLUMN repository_label TEXT;
