from __future__ import annotations

import pytest
from pydantic import ValidationError

from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.identifiers import NodeId, WorkspaceId, new_id
from personal_graph_os.domain.views import (
    ContextPack,
    FilterClause,
    FilterField,
    FilterOperator,
    ProjectionQuery,
    SavedView,
    SortClause,
    SortDirection,
    ViewKind,
)


def test_context_pack_rejects_selection_beyond_object_limit() -> None:
    with pytest.raises(InvariantViolationError):
        ContextPack(
            workspace_id=WorkspaceId(new_id()),
            name="pack",
            node_ids=(NodeId(new_id()), NodeId(new_id()), NodeId(new_id())),
            object_limit=2,
        )


def test_context_pack_accepts_selection_within_object_limit() -> None:
    pack = ContextPack(
        workspace_id=WorkspaceId(new_id()),
        name="pack",
        node_ids=(NodeId(new_id()), NodeId(new_id())),
        object_limit=5,
    )
    assert len(pack.node_ids) == 2


def test_filter_clause_rejects_a_field_outside_the_allowlist() -> None:
    with pytest.raises(ValidationError):
        FilterClause.model_validate(
            {"field": "not_a_real_field", "operator": FilterOperator.EQUALS, "value": "x"}
        )


def test_saved_view_round_trips_through_its_persisted_json_definitions() -> None:
    workspace_id = WorkspaceId(new_id())
    query = ProjectionQuery(
        filters=(
            FilterClause(
                field=FilterField.IS_ARCHIVED, operator=FilterOperator.EQUALS, value=False
            ),
        ),
        sort=(SortClause(field=FilterField.CREATED_AT, direction=SortDirection.DESC),),
        include_archived=False,
    )
    filter_definition, sort_definition = SavedView.definitions_from_query(query)
    saved_view = SavedView(
        workspace_id=workspace_id,
        name="Inbox",
        view_kind=ViewKind.TABLE,
        filter_definition=filter_definition,
        sort_definition=sort_definition,
    )

    restored = saved_view.to_projection_query()

    assert restored == query


def test_saved_view_to_projection_query_defaults_missing_definitions_to_empty() -> None:
    saved_view = SavedView(
        workspace_id=WorkspaceId(new_id()), name="Empty", view_kind=ViewKind.TABLE
    )

    restored = saved_view.to_projection_query()

    assert restored == ProjectionQuery()
