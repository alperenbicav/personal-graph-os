-- ST-04 (EP-2026-012), review finding S4-R05: a single edge type between two specific nodes is
-- the canonical instance of that relationship. Read-then-insert at the application layer cannot
-- prevent two concurrent transactions from both observing "no edge yet" and each inserting a
-- distinct row; this constraint makes "at most one" true atomically at the persistence boundary
-- regardless of caller.
--
-- Not purely additive: an existing database created before this migration could already contain
-- duplicate (workspace_id, edge_type_id, source_node_id, target_node_id) rows (no prior
-- constraint prevented it), and `CREATE UNIQUE INDEX` fails with `IntegrityError` against such
-- data (reproduced independently in a pre-0011-state diagnostic). Before creating the index,
-- deterministically retain exactly one canonical row per tuple -- the earliest by `created_at`,
-- tie-broken by `id` for a stable pick when timestamps collide -- and delete the rest. No
-- currently-passing product code path creates a duplicate, so on a database that never had one
-- this delete matches zero rows and is a no-op.
DELETE FROM edges
WHERE id NOT IN (
    SELECT id FROM (
        SELECT id, ROW_NUMBER() OVER (
            PARTITION BY workspace_id, edge_type_id, source_node_id, target_node_id
            ORDER BY created_at ASC, id ASC
        ) AS rank
        FROM edges
    )
    WHERE rank = 1
);

CREATE UNIQUE INDEX idx_edges_canonical_uniqueness
    ON edges (workspace_id, edge_type_id, source_node_id, target_node_id);
