"""Shared REST/MCP mutation-attribution recording (ST-07.2, decision #6/#8/#9 `WORK.md`).

`MutationContext` carries one mutation's attributed caller; `record_activity_event` writes
exactly one logical `ActivityEvent` into the caller's already-open `ResearchUnitOfWork`, bounds
any captured before/after snapshot, and marks it undoable only for the ST-07.3 allowlist. Reads,
failures, and no-op/reuse outcomes must never call this -- that decision belongs to the caller,
this module only bounds and writes what it is given.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from personal_graph_os.application.research_unit_of_work import ResearchUnitOfWork
from personal_graph_os.domain.activity import ActivityEvent, ActorKind, MutationAction
from personal_graph_os.domain.identifiers import WorkspaceId

# Reversible before/after envelopes are typed and capped at 256 KiB (ST-07.2 plan slice 2):
# an opaque, unbounded-shape entity snapshot cannot grow a stored audit row without limit.
MAX_EVENT_SNAPSHOT_BYTES = 256 * 1024

REST_SOURCE = "rest"
# Bearer capability is not identity (decision #5, `WORK.md`): every REST mutation is attributed
# to this fixed local-human actor, never to whatever bearer token happened to authenticate it.
REST_ACTOR_NAME = "human/local-user/rest"

# Undoable per the ST-07.3 allowlist: Node/Resource create/update/archive/restore; Edge/
# Placement create; Placement/SavedView/research-settings update. Everything else (schema,
# file deletion/bytes, discovery/import, workflow compounds, Context Pack deletion, and any
# other delete) stays non-undoable.
_UNDOABLE_NODE_RESOURCE_ACTIONS = frozenset(
    {
        MutationAction.CREATED,
        MutationAction.UPDATED,
        MutationAction.ARCHIVED,
        MutationAction.RESTORED,
    }
)


@dataclass(frozen=True)
class MutationContext:
    """The attributed caller of one mutation, transport-neutral (decision #6, `WORK.md`)."""

    actor_kind: ActorKind
    actor_name: str
    source: str
    reason: str | None = None
    session_id: str | None = None
    request_id: str | None = None

    @classmethod
    def rest(cls, *, reason: str | None = None) -> MutationContext:
        return cls(
            actor_kind=ActorKind.HUMAN,
            actor_name=REST_ACTOR_NAME,
            source=REST_SOURCE,
            reason=reason,
        )


def _bounded_snapshot(state: dict[str, object] | None) -> dict[str, object] | None:
    if state is None:
        return None
    serialized = json.dumps(state, default=str)
    if len(serialized.encode("utf-8")) <= MAX_EVENT_SNAPSHOT_BYTES:
        return state
    # An oversized snapshot is bounded, never dropped or silently truncated mid-value: the
    # event still proves a mutation happened, but the snapshot itself is marked unusable for
    # a safe compensating undo (ST-07.3 requires an exact before-state match).
    return {"_snapshot_omitted_oversized": True}


def _is_undoable(entity_type: str, action: MutationAction) -> bool:
    if entity_type in ("node", "resource"):
        return action in _UNDOABLE_NODE_RESOURCE_ACTIONS
    if entity_type in ("edge", "placement") and action is MutationAction.CREATED:
        return True
    if entity_type in ("placement", "saved_view", "research_settings"):
        return action is MutationAction.UPDATED
    return False


def record_activity_event(
    unit_of_work: ResearchUnitOfWork,
    *,
    workspace_id: WorkspaceId,
    context: MutationContext,
    entity_type: str,
    entity_id: str,
    action: MutationAction,
    before_state: dict[str, object] | None = None,
    after_state: dict[str, object] | None = None,
) -> None:
    event = ActivityEvent(
        workspace_id=workspace_id,
        actor_kind=context.actor_kind,
        actor_name=context.actor_name,
        source=context.source,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        session_id=context.session_id,
        reason=context.reason,
        before_state=_bounded_snapshot(before_state),
        after_state=_bounded_snapshot(after_state),
        is_undoable=_is_undoable(entity_type, action),
        request_id=context.request_id,
    )
    unit_of_work.activity_events.save_without_commit(event)
