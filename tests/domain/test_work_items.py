from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.identifiers import (
    NodeId,
    WorkItemId,
    WorkspaceId,
    new_id,
)
from personal_graph_os.domain.work_items import (
    WorkItem,
    WorkItemChecklistItem,
    WorkItemKind,
    WorkItemPriority,
    WorkItemStatus,
    WorkItemType,
)


def _work_item(**overrides: Any) -> WorkItem:
    defaults: dict[str, Any] = {
        "workspace_id": WorkspaceId(new_id()),
        "node_id": NodeId(new_id()),
        "kind": WorkItemKind.STORY,
        "work_type": WorkItemType.FEATURE,
        "source": "manual",
    }
    defaults.update(overrides)
    return WorkItem(**defaults)


def test_work_item_defaults_to_backlog() -> None:
    assert _work_item().status is WorkItemStatus.BACKLOG


def test_epic_may_not_declare_a_parent() -> None:
    with pytest.raises(InvariantViolationError):
        _work_item(kind=WorkItemKind.EPIC, parent_id=WorkItemId(new_id()))


def test_epic_without_a_parent_is_valid() -> None:
    epic = _work_item(kind=WorkItemKind.EPIC)
    assert epic.parent_id is None


def test_story_may_declare_an_epic_parent() -> None:
    story = _work_item(kind=WorkItemKind.STORY, parent_id=WorkItemId(new_id()))
    assert story.parent_id is not None


def test_work_item_rejects_being_its_own_parent() -> None:
    work_item_id = WorkItemId(new_id())
    with pytest.raises(InvariantViolationError):
        WorkItem(
            id=work_item_id,
            workspace_id=WorkspaceId(new_id()),
            node_id=NodeId(new_id()),
            kind=WorkItemKind.TASK,
            work_type=WorkItemType.FIX,
            parent_id=work_item_id,
            source="manual",
        )


def test_work_item_rejects_empty_source() -> None:
    with pytest.raises(InvariantViolationError):
        _work_item(source=" ")


def test_work_type_is_orthogonal_to_kind() -> None:
    """Review finding R01: work classification (feature/fix/refactor/research/ops/docs) is a
    separate selector from the Epic/Story/Task hierarchy level."""
    epic = _work_item(kind=WorkItemKind.EPIC, work_type=WorkItemType.RESEARCH)
    task = _work_item(kind=WorkItemKind.TASK, work_type=WorkItemType.OPS)
    assert epic.work_type is WorkItemType.RESEARCH
    assert task.work_type is WorkItemType.OPS


def test_repository_association_is_a_stable_node_reference() -> None:
    """Review finding R01: `repository_node_id` is a node identity, not a free-text name that
    a rename or duplicate could silently detach."""
    repository_node_id = NodeId(new_id())
    work_item = _work_item(repository_node_id=repository_node_id)
    assert work_item.repository_node_id == repository_node_id
    assert not hasattr(work_item, "repository_name")


def test_work_item_carries_the_planning_fields() -> None:
    work_item = _work_item(
        priority=WorkItemPriority.HIGH,
        due_date=date(2026, 9, 1),
        assignee="Ada",
        blockers="Waiting on the corpus refresh",
        progress_percent=40,
    )
    assert work_item.priority is WorkItemPriority.HIGH
    assert work_item.due_date == date(2026, 9, 1)
    assert work_item.assignee == "Ada"
    assert work_item.blockers == "Waiting on the corpus refresh"
    assert work_item.progress_percent == 40


def test_planning_fields_default_to_unset() -> None:
    work_item = _work_item()
    assert work_item.priority is None
    assert work_item.due_date is None
    assert work_item.assignee is None
    assert work_item.blockers is None
    assert work_item.progress_percent is None


def test_work_item_rejects_out_of_range_progress() -> None:
    for invalid in (-1, 101):
        with pytest.raises(InvariantViolationError):
            _work_item(progress_percent=invalid)


def test_work_item_rejects_blank_assignee_or_blockers() -> None:
    with pytest.raises(InvariantViolationError):
        _work_item(assignee="  ")
    with pytest.raises(InvariantViolationError):
        _work_item(blockers="  ")


def test_checklist_item_requires_a_non_empty_label() -> None:
    with pytest.raises(InvariantViolationError):
        WorkItemChecklistItem(work_item_id=WorkItemId(new_id()), position=0, label=" ")


def test_checklist_item_rejects_a_negative_position() -> None:
    with pytest.raises(InvariantViolationError):
        WorkItemChecklistItem(work_item_id=WorkItemId(new_id()), position=-1, label="x")


def test_checklist_item_defaults_to_uncompleted_and_assigns_an_id() -> None:
    item = WorkItemChecklistItem(work_item_id=WorkItemId(new_id()), position=3, label="Review")
    assert item.id is not None
    assert isinstance(item.id, str)
    assert item.position == 3
    assert item.is_completed is False
