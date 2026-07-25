"""A deterministic `WorkPlanningProvider` test double: no network call, no model, no real cost
(EP-2026-012 ST-05). A real LLM-backed classifier is separate, future, separately-approved scope.

Every returned hierarchy/plan-body value is either the caller-supplied `title`/`source_text` or
an explicit, test-supplied constructor argument -- never derived by parsing instructions out of
`source_text` -- so nothing an adversarial captured document says can change whether a plan is
"justified" or what hierarchy gets proposed.
"""

from __future__ import annotations

from personal_graph_os.domain.work_items import WorkItemType
from personal_graph_os.domain.work_planning import (
    ProposedEpic,
    ProposedStory,
    ProposedTask,
    WorkPlanResult,
)

_DEFAULT_REASON_JUSTIFIED = "configured test provider: plan is justified"
_DEFAULT_REASON_NOT_JUSTIFIED = "configured test provider: plan is not justified"


class FakeWorkPlanningProvider:
    def __init__(
        self,
        *,
        name: str = "fake",
        is_justified: bool = True,
        reason: str | None = None,
        work_type: WorkItemType = WorkItemType.FEATURE,
        story_titles: tuple[str, ...] = ("Story 1",),
        task_titles_per_story: tuple[str, ...] = ("Task 1",),
    ) -> None:
        self._name = name
        self._is_justified = is_justified
        self._reason = reason
        self._work_type = work_type
        self._story_titles = story_titles
        self._task_titles_per_story = task_titles_per_story

    @property
    def name(self) -> str:
        return self._name

    def classify(self, *, source_text: str, title: str) -> WorkPlanResult:
        if not self._is_justified:
            return WorkPlanResult(
                is_justified=False, reason=self._reason or _DEFAULT_REASON_NOT_JUSTIFIED
            )

        tasks = tuple(
            ProposedTask(title=task_title, work_type=self._work_type)
            for task_title in self._task_titles_per_story
        )
        stories = tuple(
            ProposedStory(title=story_title, work_type=self._work_type, tasks=tasks)
            for story_title in self._story_titles
        )
        return WorkPlanResult(
            is_justified=True,
            reason=self._reason or _DEFAULT_REASON_JUSTIFIED,
            epic=ProposedEpic(
                title=title, work_type=self._work_type, description=source_text[:500]
            ),
            stories=stories,
            plan_title=f"Plan: {title}",
            plan_body_markdown=f"# {title}\n\n{source_text}",
        )
