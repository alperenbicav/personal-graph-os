from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.identifiers import NodeId, WorkspaceId, new_id
from personal_graph_os.domain.resource import Resource, ResourceKind, ResourceLifecycleStatus


def _resource(
    *,
    lifecycle_status: ResourceLifecycleStatus = ResourceLifecycleStatus.INBOX,
    next_action: str | None = None,
    next_action_dismissed: bool = False,
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
        next_action_dismissed=next_action_dismissed,
        review_at=review_at,
        last_activity_at=last_activity_at if last_activity_at is not None else datetime.now(UTC),
    )


@pytest.mark.parametrize("value", [0, 1, 50, 99, 100])
def test_progress_percent_accepts_the_full_bounded_range(value: int) -> None:
    resource = Resource(
        workspace_id=WorkspaceId(new_id()),
        node_id=NodeId(new_id()),
        kind=ResourceKind.PAPER,
        canonical_identifier="arxiv:2401.00001",
        progress_percent=value,
    )
    assert resource.progress_percent == value


@pytest.mark.parametrize("value", [-1, 101, 1000])
def test_progress_percent_rejects_out_of_bounds_values(value: int) -> None:
    with pytest.raises(InvariantViolationError):
        Resource(
            workspace_id=WorkspaceId(new_id()),
            node_id=NodeId(new_id()),
            kind=ResourceKind.PAPER,
            canonical_identifier="arxiv:2401.00001",
            progress_percent=value,
        )


@pytest.mark.parametrize("value", [True, False, 1.5, "50"])
def test_progress_percent_rejects_non_integer_types(value: object) -> None:
    """A bool is an `int` subclass in Python; without strict typing, Pydantic would silently
    coerce `True`/`False` into 1/0 progress instead of rejecting the type mismatch."""
    with pytest.raises(ValidationError):
        Resource.model_validate(
            {
                "workspace_id": WorkspaceId(new_id()),
                "node_id": NodeId(new_id()),
                "kind": ResourceKind.PAPER,
                "canonical_identifier": "arxiv:2401.00001",
                "progress_percent": value,
            }
        )


def test_progress_percent_defaults_to_none() -> None:
    assert _resource().progress_percent is None


def test_paused_resource_requires_a_next_action_or_explicit_dismissal() -> None:
    with pytest.raises(InvariantViolationError):
        _resource(lifecycle_status=ResourceLifecycleStatus.PAUSED)


def test_paused_resource_with_next_action_is_valid() -> None:
    resource = _resource(
        lifecycle_status=ResourceLifecycleStatus.PAUSED, next_action="re-read section 3"
    )
    assert resource.next_action == "re-read section 3"


def test_paused_resource_with_explicit_dismissal_and_no_next_action_is_valid() -> None:
    resource = _resource(
        lifecycle_status=ResourceLifecycleStatus.PAUSED, next_action_dismissed=True
    )
    assert resource.next_action is None
    assert resource.next_action_dismissed is True


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
