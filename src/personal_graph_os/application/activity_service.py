"""Read path over the append-only `activity_events` audit trail (ST-07.1).

Transport-neutral: no FastAPI, MCP, SQLite, or filesystem import. REST and MCP each shape
this service's output into their own response/tool-result contract.
"""

from __future__ import annotations

from personal_graph_os.application.repositories import ActivityEventRepository
from personal_graph_os.domain.activity import ActivityEvent
from personal_graph_os.domain.identifiers import ActivityEventId, WorkspaceId

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100


class ActivityEventNotFoundError(Exception):
    def __init__(self, event_id: ActivityEventId) -> None:
        super().__init__(f"ActivityEvent {event_id} not found")
        self.event_id = event_id


class InvalidActivityCursorError(ValueError):
    """Raised when a caller-supplied cursor is malformed (ST07-F06): mapped to a stable 4xx
    validation response, never an uncaught 500."""


class ActivityEventPage:
    """A bounded page of events plus the cursor to fetch the next older page."""

    def __init__(self, events: tuple[ActivityEvent, ...], *, has_more: bool) -> None:
        self.events = events
        self.has_more = has_more

    @property
    def next_cursor(self) -> str | None:
        if not self.has_more or not self.events:
            return None
        last = self.events[-1]
        return _encode_cursor(last.occurred_at.isoformat(), str(last.id))


def _encode_cursor(occurred_at: str, event_id: str) -> str:
    return f"{occurred_at}|{event_id}"


def _decode_cursor(cursor: str) -> tuple[str, str]:
    occurred_at, _, event_id = cursor.partition("|")
    if not occurred_at or not event_id:
        raise InvalidActivityCursorError(f"malformed activity cursor: {cursor!r}")
    return occurred_at, event_id


def _bounded_limit(limit: int) -> int:
    if limit <= 0:
        return DEFAULT_PAGE_SIZE
    return min(limit, MAX_PAGE_SIZE)


class ActivityService:
    """Bounded, cursor-paginated reads over one workspace's activity feed."""

    def __init__(self, activity_events: ActivityEventRepository) -> None:
        self._activity_events = activity_events

    def get_event(self, workspace_id: WorkspaceId, event_id: ActivityEventId) -> ActivityEvent:
        event = self._activity_events.get(event_id)
        if event is None or event.workspace_id != workspace_id:
            raise ActivityEventNotFoundError(event_id)
        return event

    def is_already_reversed(self, event_id: ActivityEventId) -> bool:
        """Whether some compensating event already reverses `event_id` (ST07-F05 re-review):
        needed to distinguish an `already_reversed` disabled-undo reason from every other one
        in read-only views, the same way `UndoService` already checks before an undo attempt."""
        return self._activity_events.get_by_reverses(event_id) is not None

    def list_workspace_events(
        self,
        workspace_id: WorkspaceId,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> ActivityEventPage:
        bounded = _bounded_limit(limit)
        before_occurred_at, before_id = (None, None)
        if cursor is not None:
            before_occurred_at, before_id = _decode_cursor(cursor)
        events = self._activity_events.list_by_workspace(
            workspace_id,
            limit=bounded + 1,
            before_occurred_at=before_occurred_at,
            before_id=before_id,
        )
        has_more = len(events) > bounded
        return ActivityEventPage(events[:bounded], has_more=has_more)

    def list_entity_events(
        self,
        entity_type: str,
        entity_id: str,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> ActivityEventPage:
        bounded = _bounded_limit(limit)
        before_occurred_at, before_id = (None, None)
        if cursor is not None:
            before_occurred_at, before_id = _decode_cursor(cursor)
        events = self._activity_events.list_by_entity(
            entity_type,
            entity_id,
            limit=bounded + 1,
            before_occurred_at=before_occurred_at,
            before_id=before_id,
        )
        has_more = len(events) > bounded
        return ActivityEventPage(events[:bounded], has_more=has_more)
