"""Offline `ClickUpClient` for contract tests (EP-2026-012 ST-10): serves pre-seeded tasks from
memory, exactly like the real client's `get_task`, so service/API/MCP tests never touch a live
ClickUp API and never send any mutation anywhere."""

from __future__ import annotations

from collections.abc import Mapping

from personal_graph_os.application.clickup_adapters import (
    ClickUpTask,
    ClickUpTaskNotFoundError,
)


class FakeClickUpClient:
    """`ClickUpClient` backed by an in-memory task map, for offline contract tests."""

    def __init__(self, tasks: Mapping[str, ClickUpTask]) -> None:
        self._tasks = dict(tasks)

    def get_task(self, task_id: str) -> ClickUpTask:
        try:
            return self._tasks[task_id]
        except KeyError as error:
            raise ClickUpTaskNotFoundError(f"ClickUp task {task_id!r} does not exist") from error
