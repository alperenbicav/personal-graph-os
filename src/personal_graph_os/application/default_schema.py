"""Default schema seeded into a freshly created workspace.

Pure domain construction (`NodeType`/`FieldDefinition`/`StatusDefinition`/`EdgeType`/
`Workspace` from `personal_graph_os.domain`) — it has no persistence dependency, so it
lives in the application layer rather than under `infrastructure/sqlite/`, keeping
`domain`/`application` free of any concrete adapter import.

Schema is user data (product principle 3): everything here is editable afterwards
through the same tables an ST-03 schema editor will write to. This seed only ensures a
new workspace is immediately usable for capture, task tracking, and the research
workflow described in WORK.md's primary workflows, without requiring setup first.
"""

from __future__ import annotations

from personal_graph_os.domain.schema import (
    EdgeType,
    FieldDefinition,
    FieldType,
    NodeType,
    StatusDefinition,
    Workspace,
)


def _task_node_type() -> NodeType:
    return NodeType(
        name="Task",
        icon="check-square",
        color_hex="#2563eb",
        field_definitions=(
            FieldDefinition(
                name="priority",
                field_type=FieldType.SELECT,
                select_options=("low", "medium", "high"),
            ),
            FieldDefinition(name="due_date", field_type=FieldType.DATE),
        ),
        status_definitions=(
            StatusDefinition(name="todo", color_hex="#9ca3af", sort_order=0),
            StatusDefinition(name="in_progress", color_hex="#f59e0b", sort_order=1),
            StatusDefinition(name="done", color_hex="#22c55e", is_terminal=True, sort_order=2),
        ),
    )


def _note_node_type() -> NodeType:
    return NodeType(
        name="Note",
        icon="file-text",
        color_hex="#6b7280",
        status_definitions=(
            StatusDefinition(name="inbox", color_hex="#9ca3af", sort_order=0),
            StatusDefinition(name="reviewed", color_hex="#22c55e", is_terminal=True, sort_order=1),
        ),
    )


def _project_node_type() -> NodeType:
    return NodeType(
        name="Project",
        icon="folder",
        color_hex="#8b5cf6",
        status_definitions=(
            StatusDefinition(name="active", color_hex="#2563eb", sort_order=0),
            StatusDefinition(name="paused", color_hex="#f59e0b", sort_order=1),
            StatusDefinition(name="completed", color_hex="#22c55e", is_terminal=True, sort_order=2),
        ),
    )


def _decision_node_type() -> NodeType:
    return NodeType(
        name="Decision",
        icon="git-branch",
        color_hex="#0ea5e9",
        status_definitions=(
            StatusDefinition(name="proposed", color_hex="#9ca3af", sort_order=0),
            StatusDefinition(name="decided", color_hex="#22c55e", is_terminal=True, sort_order=1),
        ),
    )


def _resource_node_type() -> NodeType:
    """The backing node type for `Resource` (papers, repos, docs, articles, and so on)."""
    return NodeType(
        name="Resource",
        icon="book-open",
        color_hex="#f97316",
        field_definitions=(
            FieldDefinition(name="source_url", field_type=FieldType.URL),
            FieldDefinition(name="repository", field_type=FieldType.OBJECT_REFERENCE),
        ),
        status_definitions=(
            StatusDefinition(name="inbox", color_hex="#9ca3af", sort_order=0),
            StatusDefinition(name="to_review", color_hex="#f59e0b", sort_order=1),
            StatusDefinition(name="reading", color_hex="#2563eb", sort_order=2),
            StatusDefinition(name="paused", color_hex="#eab308", sort_order=3),
            StatusDefinition(name="reviewed", color_hex="#0ea5e9", sort_order=4),
            StatusDefinition(name="applied", color_hex="#22c55e", is_terminal=True, sort_order=5),
            StatusDefinition(name="archived", color_hex="#6b7280", is_terminal=True, sort_order=6),
        ),
    )


def _repository_node_type() -> NodeType:
    """An internally owned repository/codebase, distinct from an external Resource link."""
    return NodeType(
        name="Repository",
        icon="git-repository",
        color_hex="#0f172a",
        field_definitions=(FieldDefinition(name="clone_url", field_type=FieldType.URL),),
    )


def _default_edge_types() -> tuple[EdgeType, ...]:
    return (
        EdgeType(name="relates_to", inverse_name="relates_to", color_hex="#9ca3af"),
        EdgeType(name="supports", inverse_name="is_supported_by", color_hex="#22c55e"),
        EdgeType(name="contradicts", inverse_name="is_contradicted_by", color_hex="#ef4444"),
        EdgeType(name="implements", inverse_name="is_implemented_by", color_hex="#2563eb"),
        EdgeType(name="informs", inverse_name="is_informed_by", color_hex="#0ea5e9"),
        EdgeType(name="applied_in", inverse_name="applies", color_hex="#f97316"),
        EdgeType(name="cites", inverse_name="is_cited_by", color_hex="#8b5cf6"),
        EdgeType(name="blocks", inverse_name="is_blocked_by", color_hex="#dc2626"),
        EdgeType(name="part_of", inverse_name="has_part", color_hex="#6b7280"),
    )


def seed_default_schema(workspace: Workspace) -> Workspace:
    """Return a copy of `workspace` populated with the MVP's default schema.

    Never overwrites an already-configured workspace: this is only meaningful for a
    workspace with no node/edge types yet.
    """
    if workspace.node_types or workspace.edge_types:
        return workspace

    return workspace.model_copy(
        update={
            "node_types": (
                _task_node_type(),
                _note_node_type(),
                _project_node_type(),
                _decision_node_type(),
                _resource_node_type(),
                _repository_node_type(),
            ),
            "edge_types": _default_edge_types(),
        }
    )
