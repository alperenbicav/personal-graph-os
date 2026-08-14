from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from personal_graph_os.application.clickup_adapters import (
    ClickUpAccessDeniedError,
    ClickUpFetchFailedError,
    ClickUpTaskNotFoundError,
)
from personal_graph_os.infrastructure.clickup.clickup_client import HttpClickUpClient


def _client(handler) -> HttpClickUpClient:
    return HttpClickUpClient(
        api_token="token",
        base_url="https://api.clickup.com/api/v2",
        transport=httpx.MockTransport(handler),
    )


def test_get_task_maps_the_api_response_into_a_typed_task() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/task/task-1"
        assert request.headers["Authorization"] == "token"
        return httpx.Response(
            200,
            json={
                "id": "task-1",
                "name": "Implement signup",
                "description": "Add auth",
                "url": "https://app.clickup.com/t/task-1",
                "date_updated": 1720000000000,
            },
        )

    task = _client(handler).get_task("task-1")

    assert task.id == "task-1"
    assert task.name == "Implement signup"
    assert task.description == "Add auth"
    assert task.url == "https://app.clickup.com/t/task-1"
    assert task.date_updated == datetime.fromtimestamp(1720000000, tz=UTC)


def test_get_task_tolerates_a_missing_description_and_url() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"id": "task-1", "name": "Bare task", "date_updated": 1720000000000},
        )

    task = _client(handler).get_task("task-1")

    assert task.name == "Bare task"
    assert task.description == ""
    assert task.url is None


@pytest.mark.parametrize("status_code", (401, 403))
def test_get_task_raises_access_denied_on_401_and_403(status_code: int) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={})

    with pytest.raises(ClickUpAccessDeniedError):
        _client(handler).get_task("task-1")


def test_get_task_raises_not_found_on_404() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    with pytest.raises(ClickUpTaskNotFoundError):
        _client(handler).get_task("missing")


def test_get_task_raises_fetch_failed_on_an_unexpected_status() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    with pytest.raises(ClickUpFetchFailedError):
        _client(handler).get_task("task-1")


def test_get_task_raises_fetch_failed_on_malformed_json() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    with pytest.raises(ClickUpFetchFailedError):
        _client(handler).get_task("task-1")


def test_get_task_raises_fetch_failed_on_an_unexpected_shape() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    with pytest.raises(ClickUpFetchFailedError):
        _client(handler).get_task("task-1")


def test_get_task_raises_fetch_failed_on_a_transport_timeout() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    with pytest.raises(ClickUpFetchFailedError):
        _client(handler).get_task("task-1")
