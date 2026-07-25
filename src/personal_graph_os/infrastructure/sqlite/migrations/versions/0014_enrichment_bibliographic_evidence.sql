-- ST-06 (EP-2026-012), review finding S6-R01: ST-03 already extracts authors, publication date,
-- and abstract, but ST-04 persistence discarded them, and only opaque evidence content hashes
-- were retained with no inspectable adapter/source/retrieval provenance. Additive columns on the
-- existing version table -- authors/abstract are copied verbatim from `ExtractedContent`, never
-- asked of or trusted from the classifier; `cited_evidence_json` mirrors `evidence_content_hashes`
-- with adapter/source/retrieval provenance attached, never the raw fetched content itself.
ALTER TABLE resource_enrichment_profile_versions ADD COLUMN authors_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE resource_enrichment_profile_versions ADD COLUMN published_at TEXT;
ALTER TABLE resource_enrichment_profile_versions ADD COLUMN abstract TEXT;
ALTER TABLE resource_enrichment_profile_versions ADD COLUMN cited_evidence_json TEXT NOT NULL DEFAULT '[]';
