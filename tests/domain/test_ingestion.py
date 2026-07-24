from __future__ import annotations

from typing import Any

import pytest

from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.identifiers import WorkspaceId, new_id
from personal_graph_os.domain.ingestion import IngestionJob, IngestionJobStatus, IngestionStage


def _job(**overrides: Any) -> IngestionJob:
    defaults: dict[str, Any] = {
        "workspace_id": WorkspaceId(new_id()),
        "source": "telegram",
        "source_identifier": "update:123",
    }
    defaults.update(overrides)
    return IngestionJob(**defaults)


def test_job_starts_received_and_pending() -> None:
    job = _job()
    assert job.stage is IngestionStage.RECEIVED
    assert job.status is IngestionJobStatus.PENDING


def test_job_rejects_empty_source_identifier() -> None:
    with pytest.raises(InvariantViolationError):
        _job(source_identifier=" ")


def test_advance_to_moves_forward_exactly_one_stage() -> None:
    job = _job()
    advanced = job.advance_to(IngestionStage.NORMALIZED)
    assert advanced.stage is IngestionStage.NORMALIZED
    assert advanced.status is IngestionJobStatus.PENDING
    assert advanced.id == job.id


def test_advance_to_rejects_skipping_a_stage() -> None:
    """Review finding R03: even a no-op stage must be recorded explicitly, never skipped."""
    job = _job()
    with pytest.raises(InvariantViolationError):
        job.advance_to(IngestionStage.EXTRACTED)


def test_advance_to_rejects_going_backward() -> None:
    job = _job().advance_to(IngestionStage.NORMALIZED)
    with pytest.raises(InvariantViolationError):
        job.advance_to(IngestionStage.RECEIVED)


def test_advance_to_rejects_staying_on_the_same_stage() -> None:
    job = _job()
    with pytest.raises(InvariantViolationError):
        job.advance_to(IngestionStage.RECEIVED)


def test_advance_to_committed_requires_a_result_entity_pair() -> None:
    job = (
        _job()
        .advance_to(IngestionStage.NORMALIZED)
        .advance_to(IngestionStage.EXTRACTED)
        .advance_to(IngestionStage.ENRICHED)
        .advance_to(IngestionStage.LINKED)
    )
    with pytest.raises(InvariantViolationError):
        job.advance_to(IngestionStage.COMMITTED)
    with pytest.raises(InvariantViolationError):
        job.advance_to(IngestionStage.COMMITTED, result_entity_type="document")


def test_advance_to_committed_with_a_result_pair_succeeds() -> None:
    job = (
        _job()
        .advance_to(IngestionStage.NORMALIZED)
        .advance_to(IngestionStage.EXTRACTED)
        .advance_to(IngestionStage.ENRICHED)
        .advance_to(IngestionStage.LINKED)
        .advance_to(
            IngestionStage.COMMITTED, result_entity_type="document", result_entity_id="doc-1"
        )
    )
    assert job.stage is IngestionStage.COMMITTED
    assert job.status is IngestionJobStatus.SUCCEEDED
    assert job.result_entity_id == "doc-1"


def test_committed_stage_rejects_a_non_succeeded_status() -> None:
    """Review finding R03: committed only ever means succeeded -- a job that failed, or is
    still pending/running, must stay recorded at its actual earlier stage."""
    for status in (IngestionJobStatus.PENDING, IngestionJobStatus.RUNNING):
        with pytest.raises(InvariantViolationError):
            _job(
                stage=IngestionStage.COMMITTED,
                status=status,
                result_entity_type="document",
                result_entity_id="doc-1",
            )
    with pytest.raises(InvariantViolationError):
        _job(stage=IngestionStage.COMMITTED, status=IngestionJobStatus.FAILED, error_message="x")


def test_succeeded_status_requires_the_committed_stage() -> None:
    """Review finding R03: success is only legal once every checkpoint has been recorded."""
    with pytest.raises(InvariantViolationError):
        _job(stage=IngestionStage.LINKED, status=IngestionJobStatus.SUCCEEDED)


def test_committed_and_succeeded_job_requires_a_result_entity() -> None:
    with pytest.raises(InvariantViolationError):
        _job(stage=IngestionStage.COMMITTED, status=IngestionJobStatus.SUCCEEDED)


def test_committed_and_succeeded_job_with_result_entity_is_valid() -> None:
    job = _job(
        stage=IngestionStage.COMMITTED,
        status=IngestionJobStatus.SUCCEEDED,
        result_entity_type="document",
        result_entity_id="doc-1",
    )
    assert job.result_entity_id == "doc-1"


def test_failed_job_requires_an_error_message() -> None:
    with pytest.raises(InvariantViolationError):
        _job(status=IngestionJobStatus.FAILED)


def test_failed_job_with_error_message_is_valid() -> None:
    job = _job(status=IngestionJobStatus.FAILED, error_message="fetch timed out")
    assert job.error_message == "fetch timed out"


def test_advance_to_rejects_a_failed_job() -> None:
    """Review finding R03 (remaining delta): a failed job cannot advance to the next stage --
    it must be retried first, never silently carried forward with a stale error."""
    failed = _job(
        stage=IngestionStage.EXTRACTED,
        status=IngestionJobStatus.FAILED,
        error_message="parser crashed",
    )
    with pytest.raises(InvariantViolationError):
        failed.advance_to(IngestionStage.ENRICHED)


def test_retry_re_enters_pending_at_the_current_stage_and_clears_the_error() -> None:
    """Review finding R03 (remaining delta): `retry()` re-attempts the current stage --
    clearing `FAILED` back to `pending` at the same stage, never rewinding the stage itself."""
    failed = _job(
        stage=IngestionStage.EXTRACTED,
        status=IngestionJobStatus.FAILED,
        error_message="parser crashed",
    )
    retried = failed.retry()
    assert retried.stage is IngestionStage.EXTRACTED
    assert retried.status is IngestionJobStatus.PENDING
    assert retried.error_message is None
    assert retried.id == failed.id


def test_retry_then_advance_moves_forward_from_the_preserved_stage() -> None:
    failed = _job(
        stage=IngestionStage.EXTRACTED,
        status=IngestionJobStatus.FAILED,
        error_message="parser crashed",
    )
    advanced = failed.retry().advance_to(IngestionStage.ENRICHED)
    assert advanced.stage is IngestionStage.ENRICHED
    assert advanced.status is IngestionJobStatus.PENDING


def test_retry_rejects_a_job_that_is_not_failed() -> None:
    with pytest.raises(InvariantViolationError):
        _job().retry()
