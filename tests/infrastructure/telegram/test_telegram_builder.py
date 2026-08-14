from __future__ import annotations

import httpx

from personal_graph_os.infrastructure.telegram.builder import build_telegram_client_from_env
from personal_graph_os.infrastructure.telegram.telegram_client import HttpTelegramClient


def test_returns_none_when_telegram_is_not_enabled() -> None:
    assert (
        build_telegram_client_from_env(
            {
                "PGOS_TELEGRAM_API_TOKEN": "token",
                "PGOS_TELEGRAM_ALLOWED_CHAT_IDS": "1",
            }
        )
        is None
    )
    assert (
        build_telegram_client_from_env(
            {"PGOS_TELEGRAM_ENABLED": "0", "PGOS_TELEGRAM_API_TOKEN": "token"}
        )
        is None
    )


def test_returns_none_when_the_token_is_missing() -> None:
    assert (
        build_telegram_client_from_env(
            {"PGOS_TELEGRAM_ENABLED": "1", "PGOS_TELEGRAM_ALLOWED_CHAT_IDS": "1"}
        )
        is None
    )


def test_returns_none_when_the_chat_allowlist_is_empty() -> None:
    assert (
        build_telegram_client_from_env(
            {"PGOS_TELEGRAM_ENABLED": "1", "PGOS_TELEGRAM_API_TOKEN": "token"}
        )
        is None
    )


def test_builds_the_http_client_when_fully_configured() -> None:
    client = build_telegram_client_from_env(
        {
            "PGOS_TELEGRAM_ENABLED": "1",
            "PGOS_TELEGRAM_API_TOKEN": "token",
            "PGOS_TELEGRAM_ALLOWED_CHAT_IDS": "1, 2",
        }
    )

    assert isinstance(client, HttpTelegramClient)
    assert client.is_chat_allowed(1) is True
    assert client.is_chat_allowed(2) is True
    assert client.is_chat_allowed(3) is False


def test_the_configured_api_url_is_used_for_requests() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.host)
        return httpx.Response(200, json={"ok": True, "result": []})

    client = build_telegram_client_from_env(
        {
            "PGOS_TELEGRAM_ENABLED": "1",
            "PGOS_TELEGRAM_API_TOKEN": "token",
            "PGOS_TELEGRAM_ALLOWED_CHAT_IDS": "1",
            "PGOS_TELEGRAM_API_URL": "https://telegram.example.test",
        },
        transport=httpx.MockTransport(handler),
    )

    assert client is not None
    client.get_updates(offset=None, timeout_seconds=25)

    assert requested == ["telegram.example.test"]
