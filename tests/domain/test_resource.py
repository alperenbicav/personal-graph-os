from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.identifiers import NodeId, WorkspaceId, new_id
from personal_graph_os.domain.resource import Resource, ResourceKind, ResourceLifecycleStatus


def _resource(
    *,
    lifecycle_status: ResourceLifecycleStatus = ResourceLifecycleStatus.INBOX,
    next_action: str | None = None,
    review_at: datetime | None = None,
    last_activity_at: datetime | None = None,
) -> Resource:
    return Resource(
        workspace_id=WorkspaceId(new_id()),
        node_id=NodeId(new_id()),
        kind=ResourceKind.PAPER,
        canonical_identifier="arxiv:2401.00001",
        lifecycle_status=lifecycle_status,
        next_action=next_action,
        review_at=review_at,
        last_activity_at=last_activity_at if last_activity_at is not None else datetime.now(UTC),
    )


def test_paused_resource_requires_a_next_action() -> None:
    with pytest.raises(InvariantViolationError):
        _resource(lifecycle_status=ResourceLifecycleStatus.PAUSED)


def test_paused_resource_with_next_action_is_valid() -> None:
    resource = _resource(
        lifecycle_status=ResourceLifecycleStatus.PAUSED, next_action="re-read section 3"
    )
    assert resource.next_action == "re-read section 3"


def test_applied_resource_never_resurfaces() -> None:
    resource = _resource(
        lifecycle_status=ResourceLifecycleStatus.APPLIED,
        last_activity_at=datetime.now(UTC) - timedelta(days=365),
    )
    assert not resource.is_due_for_resurfacing(as_of=datetime.now(UTC), stale_after_days=14)


def test_resource_resurfaces_after_review_at() -> None:
    now = datetime.now(UTC)
    resource = _resource(review_at=now - timedelta(days=1), last_activity_at=now)
    assert resource.is_due_for_resurfacing(as_of=now, stale_after_days=90)


def test_resource_resurfaces_after_inactivity_without_review_at() -> None:
    now = datetime.now(UTC)
    resource = _resource(last_activity_at=now - timedelta(days=15))
    assert resource.is_due_for_resurfacing(as_of=now, stale_after_days=14)


def test_resource_does_not_resurface_before_inactivity_threshold() -> None:
    now = datetime.now(UTC)
    resource = _resource(last_activity_at=now - timedelta(days=1))
    assert not resource.is_due_for_resurfacing(as_of=now, stale_after_days=14)
