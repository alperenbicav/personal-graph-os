-- ST-02 (EP-2026-012), review finding S2-R03: a raw-inbox `Document` captured from a `file` or
-- `external_item` payload must keep that source's stable identifier recoverable -- otherwise a
-- later extraction adapter (ST-03) has no way back to the file/external item the capture came
-- from. Purely additive: every pre-existing document has `source_reference` NULL and keeps
-- rendering unchanged.

ALTER TABLE documents ADD COLUMN source_reference TEXT;
