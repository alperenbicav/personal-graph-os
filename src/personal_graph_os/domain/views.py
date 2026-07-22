"""Projections over the canonical graph: `SavedView` and `ContextPack`.

Neither holds canonical node/edge state; both describe how to select and present it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.identifiers import (
    ContextPackId,
    EdgeId,
    NodeId,
    SavedViewId,
    WorkspaceId,
    new_id,
)


class FilterField(StrEnum):
    """The allowlisted node/resource fields a `SavedView` may filter or sort by.

    A saved projection must never carry a raw SQL/filter expression (decision: "Raw
    filters/SQL in SavedView" is rejected in the epic plan); every filter/sort clause names
    one of these fields instead, so `ProjectionService` can resolve it through a fixed,
    reviewable mapping rather than evaluating caller-supplied code.
    """

    NODE_TYPE_ID = "node_type_id"
    STATUS_ID = "status_id"
    IS_ARCHIVED = "is_archived"
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"
    RESOURCE_KIND = "resource_kind"
    RESOURCE_LIFECYCLE_STATUS = "resource_lifecycle_status"
    REVIEW_AT = "review_at"
    LAST_ACTIVITY_AT = "last_activity_at"


class FilterOperator(StrEnum):
    EQUALS = "eq"
    NOT_EQUALS = "not_eq"
    IN = "in"
    BEFORE = "before"
    AFTER = "after"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"


class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


class FilterClause(BaseModel):
    """One allowlisted `field OP value` condition. `value` is unused for the null checks."""

    field: FilterField
    operator: FilterOperator
    value: object | None = None


class SortClause(BaseModel):
    field: FilterField
    direction: SortDirection = SortDirection.ASC


class ProjectionQuery(BaseModel):
    """A typed, allowlisted filter/sort request evaluated by `ProjectionService`.

    Sort clauses are applied in list order with the first clause as the primary key
    (`ProjectionService` applies them least-significant-first via a sequence of stable
    sorts so this reads naturally).
    """

    filters: tuple[FilterClause, ...] = ()
    sort: tuple[SortClause, ...] = ()
    include_archived: bool = False


class ViewKind(StrEnum):
    """The structured projections a `SavedView` may render as."""

    TABLE = "table"
    KANBAN = "kanban"
    TIMELINE = "timeline"
    CANVAS = "canvas"
    SEARCH = "search"
    ACTIVITY = "activity"


class SavedView(BaseModel):
    """A named, reusable filter/sort projection of the canonical graph."""

    id: SavedViewId = Field(default_factory=lambda: SavedViewId(new_id()))
    workspace_id: WorkspaceId
    name: str
    view_kind: ViewKind
    filter_definition: dict[str, object] = Field(default_factory=dict)
    sort_definition: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise InvariantViolationError("SavedView.name must not be empty")
        return stripped

    def to_projection_query(self) -> ProjectionQuery:
        """Reconstruct the typed, allowlisted query this saved view persists.

        Round-trips through `ProjectionQuery`'s own validation, so a `SavedView` can never
        carry a filter/sort clause naming a field outside `FilterField`.
        """
        raw_filters = self.filter_definition.get("filters", [])
        raw_sort = self.sort_definition.get("sort", [])
        assert isinstance(raw_filters, list)
        assert isinstance(raw_sort, list)
        return ProjectionQuery(
            filters=tuple(FilterClause.model_validate(clause) for clause in raw_filters),
            sort=tuple(SortClause.model_validate(clause) for clause in raw_sort),
            include_archived=bool(self.filter_definition.get("include_archived", False)),
        )

    @classmethod
    def definitions_from_query(
        cls, query: ProjectionQuery
    ) -> tuple[dict[str, object], dict[str, object]]:
        """Split a `ProjectionQuery` into the `(filter_definition, sort_definition)` pair a
        `SavedView` persists."""
        filter_definition: dict[str, object] = {
            "filters": [clause.model_dump(mode="json") for clause in query.filters],
            "include_archived": query.include_archived,
        }
        sort_definition: dict[str, object] = {
            "sort": [clause.model_dump(mode="json") for clause in query.sort]
        }
        return filter_definition, sort_definition


class ContextPack(BaseModel):
    """A reproducible, bounded selection of nodes/edges/evidence for agent handoff."""

    id: ContextPackId = Field(default_factory=lambda: ContextPackId(new_id()))
    workspace_id: WorkspaceId
    name: str
    node_ids: tuple[NodeId, ...] = ()
    edge_ids: tuple[EdgeId, ...] = ()
    evidence_pointers: tuple[str, ...] = ()
    inclusion_reasons: dict[str, str] = Field(default_factory=dict)
    object_limit: int = 200
    token_limit: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise InvariantViolationError("ContextPack.name must not be empty")
        return stripped

    @field_validator("object_limit")
    @classmethod
    def _validate_object_limit(cls, value: int) -> int:
        if value <= 0:
            raise InvariantViolationError("ContextPack.object_limit must be positive")
        return value

    def model_post_init(self, _context: object) -> None:
        total_objects = len(self.node_ids) + len(self.edge_ids)
        if total_objects > self.object_limit:
            raise InvariantViolationError(
                f"ContextPack '{self.name}' selects {total_objects} objects, "
                f"exceeding object_limit={self.object_limit}"
            )
        missing_reasons = [
            identifier
            for identifier in (*self.node_ids, *self.edge_ids, *self.evidence_pointers)
            if not self.inclusion_reasons.get(identifier, "").strip()
        ]
        if missing_reasons:
            raise InvariantViolationError(
                f"ContextPack '{self.name}' is missing an inclusion reason for: "
                f"{', '.join(missing_reasons)}"
            )
