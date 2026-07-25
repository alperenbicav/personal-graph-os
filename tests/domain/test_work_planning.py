from __future__ import annotations

import pytest

from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.identifiers import (
    DocumentId,
    DocumentVersionId,
    IngestionJobId,
    WorkItemId,
    WorkspaceId,
    new_id,
)
from personal_graph_os.domain.work_items import WorkItemType
from personal_graph_os.domain.work_planning import (
    MAX_REASON_LENGTH,
    MAX_STORIES,
    MAX_TASKS_PER_STORY,
    MAX_TITLE_LENGTH,
    ProposedEpic,
    ProposedStory,
    ProposedTask,
    WorkPlanningReceipt,
    WorkPlanOutcomeStatus,
    WorkPlanResult,
)


def _task(title: str = "A task") -> ProposedTask:
    return ProposedTask(title=title, work_type=WorkItemType.FEATURE)


def _epic() -> ProposedEpic:
    return ProposedEpic(title="An epic", work_type=WorkItemType.FEATURE)


def test_not_justified_result_requires_no_hierarchy_or_plan_fields() -> None:
    result = WorkPlanResult(is_justified=False, reason="not enough substance")
    assert result.epic is None
    assert result.stories == ()
    assert result.plan_title is None
    assert result.plan_body_markdown is None


def test_not_justified_result_rejects_a_populated_epic() -> None:
    with pytest.raises(InvariantViolationError):
        WorkPlanResult(is_justified=False, reason="not enough substance", epic=_epic())


def test_justified_result_requires_epic() -> None:
    with pytest.raises(InvariantViolationError):
        WorkPlanResult(
            is_justified=True,
            reason="justified",
            stories=(ProposedStory(title="S1", work_type=WorkItemType.FEATURE, tasks=(_task(),)),),
            plan_title="Plan",
            plan_body_markdown="# Plan",
        )


def test_justified_result_requires_at_least_one_story() -> None:
    with pytest.raises(InvariantViolationError):
        WorkPlanResult(
            is_justified=True,
            reason="justified",
            epic=_epic(),
            plan_title="Plan",
            plan_body_markdown="# Plan",
        )


def test_justified_result_requires_plan_title_and_body() -> None:
    stories = (ProposedStory(title="S1", work_type=WorkItemType.FEATURE, tasks=(_task(),)),)
    with pytest.raises(InvariantViolationError):
        WorkPlanResult(is_justified=True, reason="justified", epic=_epic(), stories=stories)


def test_justified_result_accepts_a_complete_hierarchy() -> None:
    stories = (ProposedStory(title="S1", work_type=WorkItemType.FEATURE, tasks=(_task(),)),)
    result = WorkPlanResult(
        is_justified=True,
        reason="justified",
        epic=_epic(),
        stories=stories,
        plan_title="Plan",
        plan_body_markdown="# Plan",
    )
    assert result.epic is not None
    assert len(result.stories) == 1


def test_story_requires_at_least_one_task() -> None:
    with pytest.raises(InvariantViolationError):
        ProposedStory(title="S1", work_type=WorkItemType.FEATURE, tasks=())


def test_story_rejects_more_than_the_bounded_task_count() -> None:
    with pytest.raises(InvariantViolationError):
        ProposedStory(
            title="S1",
            work_type=WorkItemType.FEATURE,
            tasks=tuple(_task(f"t{i}") for i in range(MAX_TASKS_PER_STORY + 1)),
        )


def test_plan_result_rejects_more_than_the_bounded_story_count() -> None:
    stories = tuple(
        ProposedStory(title=f"S{i}", work_type=WorkItemType.FEATURE, tasks=(_task(),))
        for i in range(MAX_STORIES + 1)
    )
    with pytest.raises(InvariantViolationError):
        WorkPlanResult(
            is_justified=True,
            reason="justified",
            epic=_epic(),
            stories=stories,
            plan_title="Plan",
            plan_body_markdown="# Plan",
        )


def test_epic_title_rejects_an_oversized_value() -> None:
    with pytest.raises(InvariantViolationError):
        ProposedEpic(title="x" * (MAX_TITLE_LENGTH + 1), work_type=WorkItemType.FEATURE)


def test_reason_rejects_an_oversized_value() -> None:
    with pytest.raises(InvariantViolationError):
        WorkPlanResult(is_justified=False, reason="x" * (MAX_REASON_LENGTH + 1))


def test_title_fields_reject_blank_values() -> None:
    with pytest.raises(InvariantViolationError):
        ProposedEpic(title="   ", work_type=WorkItemType.FEATURE)
    with pytest.raises(InvariantViolationError):
        ProposedTask(title="  ", work_type=WorkItemType.FEATURE)


def _not_justified_receipt() -> WorkPlanningReceipt:
    return WorkPlanningReceipt(
        workspace_id=WorkspaceId(new_id()),
        ingestion_job_id=IngestionJobId(new_id()),
        status=WorkPlanOutcomeStatus.NOT_JUSTIFIED,
    )


def _planned_receipt() -> WorkPlanningReceipt:
    return WorkPlanningReceipt(
        workspace_id=WorkspaceId(new_id()),
        ingestion_job_id=IngestionJobId(new_id()),
        status=WorkPlanOutcomeStatus.PLANNED,
        epic_work_item_id=WorkItemId(new_id()),
        plan_document_id=DocumentId(new_id()),
        plan_document_version_id=DocumentVersionId(new_id()),
    )


def test_not_justified_receipt_carries_no_plan_fields() -> None:
    receipt = _not_justified_receipt()
    assert receipt.epic_work_item_id is None
    assert receipt.plan_document_id is None
    assert receipt.plan_document_version_id is None


def test_not_justified_receipt_rejects_a_populated_epic_id() -> None:
    with pytest.raises(InvariantViolationError):
        WorkPlanningReceipt(
            workspace_id=WorkspaceId(new_id()),
            ingestion_job_id=IngestionJobId(new_id()),
            status=WorkPlanOutcomeStatus.NOT_JUSTIFIED,
            epic_work_item_id=WorkItemId(new_id()),
        )


def test_planned_receipt_requires_all_plan_fields() -> None:
    with pytest.raises(InvariantViolationError):
        WorkPlanningReceipt(
            workspace_id=WorkspaceId(new_id()),
            ingestion_job_id=IngestionJobId(new_id()),
            status=WorkPlanOutcomeStatus.PLANNED,
            epic_work_item_id=WorkItemId(new_id()),
        )


def test_planned_receipt_accepts_a_complete_set_of_plan_fields() -> None:
    receipt = _planned_receipt()
    assert receipt.epic_work_item_id is not None
    assert receipt.plan_document_id is not None
    assert receipt.plan_document_version_id is not None
