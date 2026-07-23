"""ST06-F04 re-review: MCP read output must honor a recursive/serialized total byte bound,
not just truncate a value's direct top-level string fields, and the truncation marker must
never itself push the result over the configured byte limit. The bound is on the *serialized*
JSON form -- quotes and character escaping included -- never a raw string's UTF-8 byte length,
so every assertion below measures the same compact, non-ASCII-escaped encoding the production
code actually bounds against."""

from __future__ import annotations

import json
import sqlite3

from personal_graph_os.application.semantic_schema import ensure_semantic_schema
from personal_graph_os.application.services import new_workspace
from personal_graph_os.domain.graph import Node
from personal_graph_os.domain.schema import NodeType
from personal_graph_os.infrastructure.mcp.dto import (
    _MAX_SERIALIZED_FIELD_BYTES,
    NodeDTO,
    _bounded_field_values,
    _bounded_text,
    _bounded_text_collection,
)
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteNodeRepository,
    SqliteWorkspaceRepository,
)


def _serialized_bytes(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def test_bounded_text_never_exceeds_the_byte_limit_with_a_multibyte_marker() -> None:
    # The ellipsis in the marker is 3 UTF-8 bytes; a naive character-length reservation
    # under-counts by 2 bytes and used to push the result 2 bytes over the limit.
    oversized = "é" * 100_000  # 2 bytes/char in UTF-8
    bounded = _bounded_text(oversized, maximum_bytes=1_000)

    assert _serialized_bytes(bounded) <= 1_000
    assert bounded.endswith("…[truncated]")


def test_bounded_text_at_exactly_the_limit_is_unchanged() -> None:
    # The 2-byte JSON quote wrapper is part of the serialized contract, so the raw content
    # that exactly fits `maximum_bytes` once serialized is 2 bytes shorter than the limit.
    value = "x" * 998
    assert _serialized_bytes(value) == 1_000
    assert _bounded_text(value, maximum_bytes=1_000) == value


def test_bounded_text_bounds_the_serialized_form_not_the_raw_string() -> None:
    # A quote/backslash-heavy value expands under JSON escaping; a raw-byte bound alone would
    # under-count and let the serialized output exceed `maximum_bytes` (ST06-F04 re-review).
    adversarial = ('"\\' * 2_000) + ("€" * 2_000)
    bounded = _bounded_text(adversarial, maximum_bytes=1_000)

    assert _serialized_bytes(bounded) <= 1_000


def test_bounded_field_values_bounds_the_aggregate_total_not_just_each_value() -> None:
    # Each individual value is well under any reasonable per-field limit, but a thousand of
    # them together vastly exceed the aggregate contract -- the previous implementation
    # bounded each string independently and let the dict grow unbounded in total.
    field_values: dict[str, object] = {f"field_{i}": ("v" * 500) for i in range(1_000)}

    bounded = _bounded_field_values(field_values)

    assert _serialized_bytes(bounded) <= _MAX_SERIALIZED_FIELD_BYTES
    assert len(bounded) < len(field_values)


def test_bounded_field_values_bounds_nested_objects_and_arrays() -> None:
    field_values: dict[str, object] = {
        "outer": {
            "inner_list": ["y" * 2_000 for _ in range(200)],
            "inner_dict": {f"k{i}": "z" * 2_000 for i in range(200)},
        }
    }

    bounded = _bounded_field_values(field_values)

    assert _serialized_bytes(bounded) <= _MAX_SERIALIZED_FIELD_BYTES


def test_bounded_field_values_bounds_adversarial_escaped_and_multibyte_content() -> None:
    # Quote/backslash escaping and multibyte characters both expand under JSON serialization;
    # the exact contract must hold even when every value is adversarial in this way.
    field_values: dict[str, object] = {
        f"field_{i}": ('"\\\n' * 300) + ("𝔘" * 300) for i in range(50)
    }

    bounded = _bounded_field_values(field_values)

    assert _serialized_bytes(bounded) <= _MAX_SERIALIZED_FIELD_BYTES


def test_bounded_field_values_handles_deep_nesting_without_a_recursion_error() -> None:
    nested: dict[str, object] = {"leaf": "x" * 100}
    for _ in range(200):
        nested = {"child": nested}

    bounded = _bounded_field_values({"root": nested})

    assert isinstance(bounded, dict)
    assert _serialized_bytes(bounded) <= _MAX_SERIALIZED_FIELD_BYTES


def test_bounded_text_collection_bounds_the_aggregate_total() -> None:
    values = tuple(f"takeaway {i} " + ("t" * 1_000) for i in range(200))

    bounded = _bounded_text_collection(values)

    assert _serialized_bytes(bounded) <= _MAX_SERIALIZED_FIELD_BYTES
    assert len(bounded) < len(values)


def test_node_dto_bounds_a_pre_existing_oversized_row_written_outside_the_gateway(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """A Node written directly (e.g. via REST, or a legacy row from before the MCP write-side
    bound existed) can carry an oversized body/field_values -- the gateway's write-side
    validation never ran. The MCP read path must still bound its own output deterministically.
    """
    task_type = NodeType(name="Task")
    workspace = ensure_semantic_schema(
        new_workspace("Personal").model_copy(update={"node_types": (task_type,)})
    )
    SqliteWorkspaceRepository(sqlite_connection).save(workspace)
    node_repository = SqliteNodeRepository(sqlite_connection)
    oversized_node = Node(
        workspace_id=workspace.id,
        node_type_id=task_type.id,
        title="Legacy row",
        body="b" * 200_000,
        field_values={f"field_{i}": ("v" * 500) for i in range(1_000)},
    )
    node_repository.save(oversized_node)

    stored = node_repository.get(oversized_node.id)
    assert stored is not None
    dto = NodeDTO.from_domain(stored)

    assert _serialized_bytes(dto.body) <= _MAX_SERIALIZED_FIELD_BYTES
    assert _serialized_bytes(dto.field_values) <= _MAX_SERIALIZED_FIELD_BYTES
