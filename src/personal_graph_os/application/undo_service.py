"""Stale-safe compensating undo (ST-07.3, decision #9 `WORK.md`).

Only the allowlisted `is_undoable` events can be reversed: Node/Resource create (soft-archive)/
update/archive/restore, Edge/Placement create (remove), and Placement/SavedView/
research-settings update. Undo never edits history -- it commits a linked compensating
`ActivityEvent` (`reverses_event_id`) plus the inverse write in one transaction, and the
compensating event is itself never undoable (redo is out of scope). A caller-supplied entity
state is never accepted: the inverse is always the exact recorded `before_state`, or a
soft-archive/removal for a create.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from personal_graph_os.application.activity_recording import REST_ACTOR_NAME, REST_SOURCE
from personal_graph_os.application.repositories import (
    ActivityEventRepository,
    CanvasPlacementRepository,
    EdgeRepository,
    NodeRepository,
    ResearchSettingsRepository,
    ResourceRepository,
    SavedViewRepository,
)
from personal_graph_os.application.research_unit_of_work import ResearchUnitOfWork
from personal_graph_os.domain.activity import ActivityEvent, ActorKind, MutationAction
from personal_graph_os.domain.canvas import CanvasPlacement
from personal_graph_os.domain.graph import Node
from personal_graph_os.domain.identifiers import (
    ActivityEventId,
    CanvasPlacementId,
    EdgeId,
    NodeId,
    ResourceId,
    SavedViewId,
    WorkspaceId,
)
from personal_graph_os.domain.research_settings import WorkspaceResearchSettings
from personal_graph_os.domain.resource import Resource, ResourceLifecycleStatus
from personal_graph_os.domain.views import SavedView

MAX_UNDO_REASON_LENGTH = 1000

_NODE = "node"
_RESOURCE = "resource"
_EDGE = "edge"
_PLACEMENT = "placement"
_SAVED_VIEW = "saved_view"
_RESEARCH_SETTINGS = "research_settings"

# Undo of a create soft-archives (node/resource, which have a lifecycle) rather than removes;
# an edge/placement has no lifecycle, so undoing its create removes it outright.
_SOFT_ARCHIVE_ON_CREATE = frozenset({_NODE, _RESOURCE})
_REMOVE_ON_CREATE = frozenset({_EDGE, _PLACEMENT})


class UndoConflictError(ValueError):
    """One undo precondition failed: missing/cross-workspace, non-undoable, already reversed,
    an unusably oversized recorded snapshot, or stale (current state no longer matches the
    recorded after-state). Reported as 409, unchanged -- never a partial write."""


def _validate_reason(reason: str) -> str:
    stripped = reason.strip()
    if not stripped:
        raise UndoConflictError("undo requires a non-empty reason")
    if len(stripped) > MAX_UNDO_REASON_LENGTH:
        raise UndoConflictError(f"reason must not exceed {MAX_UNDO_REASON_LENGTH} characters")
    return stripped


class UndoService:
    """Reverses one allowlisted, still-current `ActivityEvent`."""

    def __init__(
        self,
        activity_events: ActivityEventRepository,
        nodes: NodeRepository,
        resources: ResourceRepository,
        edges: EdgeRepository,
        placements: CanvasPlacementRepository,
        saved_views: SavedViewRepository,
        research_settings: ResearchSettingsRepository,
        unit_of_work_factory: Callable[[], ResearchUnitOfWork],
    ) -> None:
        self._activity_events = activity_events
        self._nodes = nodes
        self._resources = resources
        self._edges = edges
        self._placements = placements
        self._saved_views = saved_views
        self._research_settings = research_settings
        self._unit_of_work_factory = unit_of_work_factory

    def _current_state(self, entity_type: str, entity_id: str) -> dict[str, object] | None:
        if entity_type == _NODE:
            obj = self._nodes.get(NodeId(entity_id))
        elif entity_type == _RESOURCE:
            obj = self._resources.get(ResourceId(entity_id))
        elif entity_type == _EDGE:
            obj = self._edges.get(EdgeId(entity_id))
        elif entity_type == _PLACEMENT:
            obj = self._placements.get(CanvasPlacementId(entity_id))
        elif entity_type == _SAVED_VIEW:
            obj = self._saved_views.get(SavedViewId(entity_id))
        elif entity_type == _RESEARCH_SETTINGS:
            obj = self._research_settings.get(WorkspaceId(entity_id))
        else:
            obj = None
        return None if obj is None else obj.model_dump(mode="json")

    def undo(
        self, workspace_id: WorkspaceId, event_id: ActivityEventId, *, reason: str
    ) -> ActivityEvent:
        bounded_reason = _validate_reason(reason)

        event = self._activity_events.get(event_id)
        if event is None or event.workspace_id != workspace_id:
            raise UndoConflictError(f"activity event {event_id} does not exist in this workspace")
        if event.reverses_event_id is not None:
            raise UndoConflictError(
                f"activity event {event_id} is itself a compensating event and cannot be undone"
            )
        if not event.is_undoable:
            raise UndoConflictError(f"activity event {event_id} is not undoable")
        if self._activity_events.get_by_reverses(event_id) is not None:
            raise UndoConflictError(f"activity event {event_id} was already reversed")
        if isinstance(event.after_state, dict) and event.after_state.get(
            "_snapshot_omitted_oversized"
        ):
            raise UndoConflictError(
                f"activity event {event_id} cannot be undone: its recorded snapshot was too "
                "large to store safely"
            )

        current_state = self._current_state(event.entity_type, event.entity_id)
        if current_state != event.after_state:
            raise UndoConflictError(
                f"activity event {event_id} is stale: {event.entity_type} state has changed "
                "since it was recorded"
            )

        with self._unit_of_work_factory() as unit_of_work:
            if event.action is MutationAction.CREATED:
                self._apply_create_inverse(unit_of_work, event.entity_type, event.entity_id)
                compensating_action = (
                    MutationAction.DELETED
                    if event.entity_type in _REMOVE_ON_CREATE
                    else MutationAction.ARCHIVED
                )
            else:
                self._apply_restore_inverse(
                    unit_of_work, event.entity_type, event.entity_id, event.before_state
                )
                compensating_action = MutationAction.UPDATED

            compensating_event = ActivityEvent(
                workspace_id=workspace_id,
                actor_kind=ActorKind.HUMAN,
                actor_name=REST_ACTOR_NAME,
                source=REST_SOURCE,
                entity_type=event.entity_type,
                entity_id=event.entity_id,
                action=compensating_action,
                reason=bounded_reason,
                is_undoable=False,
                reverses_event_id=event.id,
            )
            unit_of_work.activity_events.save_without_commit(compensating_event)
        return compensating_event

    def _apply_create_inverse(
        self, unit_of_work: ResearchUnitOfWork, entity_type: str, entity_id: str
    ) -> None:
        if entity_type == _NODE:
            node = unit_of_work.nodes.get(NodeId(entity_id))
            assert node is not None
            archived = node.model_copy(
                update={"is_archived": True, "updated_at": datetime.now(UTC)}
            )
            unit_of_work.nodes.save_without_commit(archived)
            return
        if entity_type == _RESOURCE:
            resource = unit_of_work.resources.get(ResourceId(entity_id))
            assert resource is not None
            archived = resource.model_copy(
                update={
                    "lifecycle_status": ResourceLifecycleStatus.ARCHIVED,
                    "last_activity_at": datetime.now(UTC),
                }
            )
            unit_of_work.resources.save_without_commit(archived)
            return
        if entity_type == _EDGE:
            unit_of_work.edges.delete_without_commit(EdgeId(entity_id))
            return
        if entity_type == _PLACEMENT:
            unit_of_work.placements.delete_without_commit(CanvasPlacementId(entity_id))
            return
        raise UndoConflictError(f"entity type {entity_type!r} has no create-undo path")

    def _apply_restore_inverse(
        self,
        unit_of_work: ResearchUnitOfWork,
        entity_type: str,
        entity_id: str,
        before_state: dict[str, object] | None,
    ) -> None:
        if before_state is None:
            raise UndoConflictError(
                f"activity event for {entity_type} {entity_id} has no recorded before-state"
            )
        if entity_type == _NODE:
            unit_of_work.nodes.save_without_commit(Node.model_validate(before_state))
            return
        if entity_type == _RESOURCE:
            unit_of_work.resources.save_without_commit(Resource.model_validate(before_state))
            return
        if entity_type == _PLACEMENT:
            unit_of_work.placements.save_without_commit(
                CanvasPlacement.model_validate(before_state)
            )
            return
        if entity_type == _SAVED_VIEW:
            unit_of_work.saved_views.save_without_commit(SavedView.model_validate(before_state))
            return
        if entity_type == _RESEARCH_SETTINGS:
            unit_of_work.research_settings.save_without_commit(
                WorkspaceResearchSettings.model_validate(before_state)
            )
            return
        raise UndoConflictError(f"entity type {entity_type!r} has no restore-undo path")
