from __future__ import annotations

import httpx

from personal_graph_os.application.clickup_adapters import ClickUpTaskNotFoundError
from personal_graph_os.infrastructure.clickup.builder import build_clickup_client_from_env
from personal_graph_os.infrastructure.clickup.clickup_client import HttpClickUpClient


def test_returns_none_when_clickup_is_not_configured() -> None:
    assert build_clickup_client_from_env({}) is None
    assert build_clickup_client_from_env({"PGOS_CLICKUP_API_TOKEN": "  "}) is None


def test_builds_the_http_client_when_a_token_is_configured() -> None:
    client = build_clickup_client_from_env({"PGOS_CLICKUP_API_TOKEN": "secret-token"})

    assert isinstance(client, HttpClickUpClient)


def test_the_configured_base_url_is_used_for_requests() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.host)
        return httpx.Response(404, json={})

    client = build_clickup_client_from_env(
        {
            "PGOS_CLICKUP_API_TOKEN": "secret-token",
            "PGOS_CLICKUP_BASE_URL": "https://clickup.example.test/api/v2",
        },
        transport=httpx.MockTransport(handler),
    )

    try:
        client.get_task("task-1")
    except ClickUpTaskNotFoundError:
        pass

    assert requested == ["clickup.example.test"]


def test_the_default_base_url_is_used_when_no_override_is_set() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.host)
        return httpx.Response(404, json={})

    client = build_clickup_client_from_env(
        {"PGOS_CLICKUP_API_TOKEN": "secret-token"},
        transport=httpx.MockTransport(handler),
    )

    try:
        client.get_task("task-1")
    except ClickUpTaskNotFoundError:
        pass

    assert requested == ["api.clickup.com"]
