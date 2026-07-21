"""Evaluates typed `ProjectionQuery` requests against canonical Node/Resource data.

Table, Kanban, and timeline views are all the same filtered/sorted row set, presented
differently; grouping (Kanban) and date-fallback ordering (timeline) are the only projection-
specific behavior. Filtering/sorting happens in Python over repository-fetched rows rather than
generated SQL, so a `SavedView`'s allowlisted `FilterField`/`FilterOperator` values are the only
thing that can ever influence the query (see `domain/views.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from personal_graph_os.application.repositories import NodeRepository, ResourceRepository
from personal_graph_os.domain.graph import Node
from personal_graph_os.domain.identifiers import WorkspaceId
from personal_graph_os.domain.resource import Resource
from personal_graph_os.domain.views import (
    FilterClause,
    FilterField,
    FilterOperator,
    ProjectionQuery,
    SortClause,
)


@dataclass(frozen=True)
class ProjectionItem:
    """One row of a projection: a `Node` plus its `Resource`, when it backs one."""

    node: Node
    resource: Resource | None


def _field_value(item: ProjectionItem, field: FilterField) -> object | None:
    if field is FilterField.NODE_TYPE_ID:
        return item.node.node_type_id
    if field is FilterField.STATUS_ID:
        return item.node.status_id
    if field is FilterField.IS_ARCHIVED:
        return item.node.is_archived
    if field is FilterField.CREATED_AT:
        return item.node.created_at
    if field is FilterField.UPDATED_AT:
        return item.node.updated_at
    if field is FilterField.RESOURCE_KIND:
        return item.resource.kind if item.resource is not None else None
    if field is FilterField.RESOURCE_LIFECYCLE_STATUS:
        return item.resource.lifecycle_status if item.resource is not None else None
    if field is FilterField.REVIEW_AT:
        return item.resource.review_at if item.resource is not None else None
    if field is FilterField.LAST_ACTIVITY_AT:
        return item.resource.last_activity_at if item.resource is not None else None
    raise AssertionError(f"unhandled FilterField {field}")  # exhaustive over the enum


def _as_datetime(value: object) -> datetime:
    """Coerce a field value or clause bound into a `datetime` for `BEFORE`/`AFTER`.

    `BEFORE`/`AFTER` only apply to the timestamp fields in `FilterField`, so a value reaching
    here is always either an actual `datetime` (from `_field_value`) or an ISO-8601 string
    (from a JSON-persisted `SavedView`'s clause bound).
    """
    if isinstance(value, datetime):
        return value
    assert isinstance(value, str)
    return datetime.fromisoformat(value)


def _matches(item: ProjectionItem, clause: FilterClause) -> bool:
    value = _field_value(item, clause.field)
    if clause.operator is FilterOperator.IS_NULL:
        return value is None
    if clause.operator is FilterOperator.IS_NOT_NULL:
        return value is not None
    if value is None:
        return False
    if clause.operator is FilterOperator.EQUALS:
        return str(value) == str(clause.value)
    if clause.operator is FilterOperator.NOT_EQUALS:
        return str(value) != str(clause.value)
    if clause.operator is FilterOperator.IN:
        candidates = clause.value if isinstance(clause.value, list) else ()
        return str(value) in {str(candidate) for candidate in candidates}
    if clause.operator is FilterOperator.BEFORE:
        assert clause.value is not None
        return _as_datetime(value) < _as_datetime(clause.value)
    if clause.operator is FilterOperator.AFTER:
        assert clause.value is not None
        return _as_datetime(value) > _as_datetime(clause.value)
    raise AssertionError(f"unhandled FilterOperator {clause.operator}")  # exhaustive


def _sort_key(value: object | None) -> tuple[int, object]:
    if value is None:
        return (1, "")
    if isinstance(value, StrEnum):
        return (0, value.value)
    return (0, value)


class ProjectionService:
    """Evaluates `ProjectionQuery` requests into table/Kanban/timeline projections."""

    def __init__(self, nodes: NodeRepository, resources: ResourceRepository) -> None:
        self._nodes = nodes
        self._resources = resources

    def _items(
        self, workspace_id: WorkspaceId, *, include_archived: bool
    ) -> tuple[ProjectionItem, ...]:
        nodes = self._nodes.list_by_workspace(workspace_id, include_archived=include_archived)
        resources_by_node_id = {
            resource.node_id: resource
            for resource in self._resources.list_by_workspace(workspace_id)
        }
        return tuple(
            ProjectionItem(node=node, resource=resources_by_node_id.get(node.id)) for node in nodes
        )

    def _sort(
        self, items: tuple[ProjectionItem, ...], sort_clauses: tuple[SortClause, ...]
    ) -> tuple[ProjectionItem, ...]:
        result = list(items)
        for clause in reversed(sort_clauses):
            result.sort(
                key=lambda item, field=clause.field: _sort_key(_field_value(item, field)),
                reverse=clause.direction.value == "desc",
            )
        return tuple(result)

    def evaluate_table(
        self, workspace_id: WorkspaceId, query: ProjectionQuery
    ) -> tuple[ProjectionItem, ...]:
        """The filtered, sorted row set every projection (table/Kanban/timeline) is built from."""
        items = self._items(workspace_id, include_archived=query.include_archived)
        filtered = tuple(
            item for item in items if all(_matches(item, clause) for clause in query.filters)
        )
        return self._sort(filtered, query.sort)

    def evaluate_kanban(
        self, workspace_id: WorkspaceId, query: ProjectionQuery, group_by: FilterField
    ) -> dict[str, tuple[ProjectionItem, ...]]:
        """Groups `evaluate_table`'s rows by `group_by` (e.g. Node status or Resource lifecycle).

        A row whose `group_by` field is unset (e.g. no status assigned) is grouped under the
        empty-string key rather than dropped, so it stays visible instead of silently
        disappearing from the board.
        """
        columns: dict[str, list[ProjectionItem]] = {}
        for item in self.evaluate_table(workspace_id, query):
            value = _field_value(item, group_by)
            key = (
                "" if value is None else (value.value if isinstance(value, StrEnum) else str(value))
            )
            columns.setdefault(key, []).append(item)
        return {key: tuple(rows) for key, rows in columns.items()}

    def evaluate_timeline(
        self, workspace_id: WorkspaceId, query: ProjectionQuery, date_field: FilterField
    ) -> tuple[ProjectionItem, ...]:
        """Orders `evaluate_table`'s rows by `date_field`, falling back to `Node.created_at`
        for a row that never set it, so nothing is dropped from the timeline for lacking an
        explicit date."""
        items = self.evaluate_table(workspace_id, query)

        def timeline_key(item: ProjectionItem) -> datetime:
            value = _field_value(item, date_field)
            if isinstance(value, datetime):
                return value
            return item.node.created_at

        return tuple(sorted(items, key=timeline_key))
