"""Real ClickUp REST client (EP-2026-012 ST-10): the narrowest read surface ClickUp needs.

Only `GET /task/{task_id}` is exposed today -- ClickUp is an ingress channel, never a write
target, so there is deliberately no create/update path and no way for this client to mutate a
ClickUp workspace. The task's `date_updated` (epoch milliseconds on the wire) is normalized to a
`datetime` so `ClickupService` can use it as the per-channel sync cursor.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx

from personal_graph_os.application.clickup_adapters import (
    ClickUpAccessDeniedError,
    ClickUpFetchFailedError,
    ClickUpTask,
    ClickUpTaskNotFoundError,
)

DEFAULT_CLICKUP_BASE_URL = "https://api.clickup.com/api/v2"
_DEFAULT_TIMEOUT_SECONDS = 15.0
_NON_SUCCESSFUL_HTTP_ERRORS = (AttributeError, KeyError, TypeError, ValueError)


class HttpClickUpClient:
    """`ClickUpClient` backed by the ClickUp REST v2 API over `httpx`.

    `transport` is a testability seam only (mirroring the provider factories): real callers
    never pass it; tests inject an `httpx.MockTransport` to prove the request/response mapping
    without a live network call.
    """

    def __init__(
        self,
        *,
        api_token: str,
        base_url: str = DEFAULT_CLICKUP_BASE_URL,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_token = api_token
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout_seconds, transport=transport)

    def get_task(self, task_id: str) -> ClickUpTask:
        url = f"{self._base_url}/task/{task_id}"
        try:
            response = self._client.get(url, headers={"Authorization": self._api_token})
        except httpx.TimeoutException as error:
            raise ClickUpFetchFailedError(
                f"ClickUp API timed out reading task {task_id!r}"
            ) from error
        except httpx.HTTPError as error:
            raise ClickUpFetchFailedError(
                f"ClickUp API request for task {task_id!r} failed: {error}"
            ) from error

        if response.status_code in (401, 403):
            raise ClickUpAccessDeniedError(
                f"ClickUp API rejected the configured token (HTTP {response.status_code})"
            )
        if response.status_code == 404:
            raise ClickUpTaskNotFoundError(
                f"ClickUp task {task_id!r} does not exist or is not visible to the token"
            )
        if response.status_code != 200:
            raise ClickUpFetchFailedError(
                f"ClickUp API returned HTTP {response.status_code} for task {task_id!r}"
            )

        try:
            payload = response.json()
        except json.JSONDecodeError as error:
            raise ClickUpFetchFailedError(
                f"ClickUp API did not return valid JSON for task {task_id!r}"
            ) from error

        try:
            return ClickUpTask(
                id=str(payload["id"]),
                name=payload["name"],
                description=payload.get("description") or "",
                url=payload.get("url"),
                date_updated=datetime.fromtimestamp(payload["date_updated"] / 1000, tz=UTC),
            )
        except _NON_SUCCESSFUL_HTTP_ERRORS as error:
            raise ClickUpFetchFailedError(
                f"ClickUp API returned task {task_id!r} in an unexpected shape: {error}"
            ) from error
