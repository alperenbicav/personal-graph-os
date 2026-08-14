"""ClickUp channel ingress (EP-2026-012 ST-10).

`ClickupService` is the one composition point for importing a user-selected ClickUp task into
the same `CaptureEnvelope` pipeline every other channel uses: it reads the task through a
`ClickUpClient`, normalizes it into a `CaptureEnvelope`, submits it through the shared
`CapturePlanningOrchestrator` (the same path MCP `pgos_capture` uses), and -- only after the
import succeeded -- advances the `clickup` channel cursor to the task's `date_updated`.

`request_id == task.id` is the stable external id: it becomes `IngestionJob.source_identifier`
and the idempotency receipt key, so re-importing the same task replays instead of duplicating,
while a task whose normalized content changed produces a typed `CaptureIdempotencyConflictError`
(review contract: reuse of a key for a different command is never a silent replay). The cursor
is pure ingress position: no mutation is ever sent back to ClickUp and no bidirectional sync is
wired.
"""

from __future__ import annotations

from collections.abc import Callable

from personal_graph_os.application.capture_planning_orchestrator import (
    CapturePlanningOrchestrator,
)
from personal_graph_os.application.capture_service import CaptureOutcome
from personal_graph_os.application.clickup_adapters import (
    ClickUpClient,
    ClickUpNotConfiguredError,
    ClickUpTask,
)
from personal_graph_os.application.repositories import WorkspaceRepository
from personal_graph_os.application.research_unit_of_work import ResearchUnitOfWork
from personal_graph_os.application.services import WorkspaceNotFoundError
from personal_graph_os.application.work_planning_service import WorkPlanOutcome
from personal_graph_os.domain.capture import (
    CaptureEnvelope,
    CaptureIntent,
    CaptureOperation,
    CaptureOperationKind,
    CapturePayloadKind,
)
from personal_graph_os.domain.channel_sync import ChannelSyncState
from personal_graph_os.domain.identifiers import NodeId, WorkspaceId

CLICKUP_CHANNEL_NAME = "clickup"
CLICKUP_SOURCE_NAME = "clickup"


class ClickupService:
    def __init__(
        self,
        clickup_client: ClickUpClient | None,
        capture_planning_orchestrator: CapturePlanningOrchestrator,
        workspace_repository: WorkspaceRepository,
        unit_of_work_factory: Callable[[], ResearchUnitOfWork],
    ) -> None:
        self._clickup_client = clickup_client
        self._capture_planning_orchestrator = capture_planning_orchestrator
        self._workspace_repository = workspace_repository
        self._unit_of_work_factory = unit_of_work_factory

    def import_item(
        self,
        workspace_id: WorkspaceId,
        *,
        task_id: str,
        intent: CaptureIntent,
        actor_name: str,
        repository_node_id: NodeId | None = None,
    ) -> tuple[CaptureOutcome, WorkPlanOutcome | None]:
        """Read one ClickUp task, import it through the shared capture pipeline, then advance
        the `clickup` sync cursor on success.

        Returns `(capture_outcome, plan_outcome)`; `plan_outcome` is `None` unless `intent` is
        `plan_work` and the orchestrator determined planning was justified (mirroring every
        other capture channel).
        """
        if self._clickup_client is None:
            raise ClickUpNotConfiguredError(
                "ClickUp is not configured: set PGOS_CLICKUP_API_TOKEN to enable imports"
            )
        self._require_workspace(workspace_id)
        task = self._clickup_client.get_task(task_id)
        envelope = self._build_envelope(workspace_id, task, intent=intent, actor_name=actor_name)
        outcome, plan_outcome = self._capture_planning_orchestrator.submit(
            envelope, repository_node_id=repository_node_id
        )
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.channel_sync_state.save_without_commit(
                ChannelSyncState(
                    workspace_id=workspace_id,
                    channel=CLICKUP_CHANNEL_NAME,
                    cursor_value=task.date_updated.isoformat(),
                )
            )
        return outcome, plan_outcome

    def _require_workspace(self, workspace_id: WorkspaceId) -> None:
        if self._workspace_repository.get(workspace_id) is None:
            raise WorkspaceNotFoundError(f"workspace {workspace_id} does not exist")

    def _build_envelope(
        self,
        workspace_id: WorkspaceId,
        task: ClickUpTask,
        *,
        intent: CaptureIntent,
        actor_name: str,
    ) -> CaptureEnvelope:
        text = _task_text(task)
        # `EXTERNAL_ITEM` models the task deterministically as its own content: the name +
        # description are stored verbatim as the body (never re-parsed as a channel message, so
        # a URL inside the prose cannot turn the task into an Article), `external_item_id` is
        # the task id, and `source_reference`/`source_identifier` both carry it. `plan_work`
        # declares `operations=(PLAN,)` structurally -- exactly like a structured MCP caller.
        return CaptureEnvelope(
            workspace_id=workspace_id,
            source=CLICKUP_SOURCE_NAME,
            request_id=task.id,
            actor_name=actor_name,
            payload_kind=CapturePayloadKind.EXTERNAL_ITEM,
            external_item_id=task.id,
            text=text,
            intent=intent,
            title=task.name,
            external_url=task.url,
            operations=(
                (CaptureOperation(kind=CaptureOperationKind.PLAN),)
                if intent is CaptureIntent.PLAN_WORK
                else ()
            ),
        )


def _task_text(task: ClickUpTask) -> str:
    if task.description.strip():
        return f"{task.name}\n\n{task.description}"
    return task.name
