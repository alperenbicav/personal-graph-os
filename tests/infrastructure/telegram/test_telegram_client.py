from __future__ import annotations

import httpx
import pytest

from personal_graph_os.application.telegram_adapters import (
    TelegramAccessDeniedError,
    TelegramFetchFailedError,
    TelegramRateLimitedError,
)
from personal_graph_os.infrastructure.telegram.telegram_client import HttpTelegramClient


def _client(handler) -> HttpTelegramClient:
    return HttpTelegramClient(
        api_token="token",
        allowed_chat_ids=frozenset({1}),
        api_url="https://api.telegram.org",
        transport=httpx.MockTransport(handler),
    )


def _message_json(message_id: int = 1, text: str = "https://example.com") -> dict[str, object]:
    return {
        "update_id": message_id,
        "message": {
            "message_id": message_id,
            "date": 1720000000,
            "chat": {"id": 1},
            "from": {"id": 2, "is_bot": False},
            "text": text,
        },
    }


def test_get_updates_maps_the_api_response_into_typed_updates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/bottoken/getUpdates"
        assert request.url.params["offset"] == "5"
        assert request.url.params["timeout"] == "25"
        return httpx.Response(200, json={"ok": True, "result": [_message_json(1)]})

    updates = _client(handler).get_updates(offset=5, timeout_seconds=25)

    assert len(updates) == 1
    assert updates[0].update_id == 1
    assert updates[0].message is not None
    assert updates[0].message.chat.id == 1
    assert updates[0].message.from_ is not None
    assert updates[0].message.from_.is_bot is False
    assert updates[0].message.text == "https://example.com"


def test_get_updates_omits_offset_when_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "offset" not in request.url.params
        return httpx.Response(200, json={"ok": True, "result": []})

    assert _client(handler).get_updates(offset=None, timeout_seconds=25) == ()


def test_send_message_posts_chat_and_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        import json

        assert request.url.path == "/bottoken/sendMessage"
        assert json.loads(request.content) == {"chat_id": 1, "text": "hello"}
        return httpx.Response(200, json={"ok": True, "result": {}})

    _client(handler).send_message(1, "hello")


@pytest.mark.parametrize("status_code", (401, 403))
def test_get_updates_raises_access_denied_on_401_and_403(status_code: int) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={})

    with pytest.raises(TelegramAccessDeniedError):
        _client(handler).get_updates(offset=None, timeout_seconds=25)


def test_get_updates_raises_rate_limited_on_429() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={})

    with pytest.raises(TelegramRateLimitedError):
        _client(handler).get_updates(offset=None, timeout_seconds=25)


def test_get_updates_raises_fetch_failed_on_an_unexpected_status() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    with pytest.raises(TelegramFetchFailedError):
        _client(handler).get_updates(offset=None, timeout_seconds=25)


def test_get_updates_raises_fetch_failed_when_the_body_says_ok_false() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "description": "bot was blocked"})

    with pytest.raises(TelegramFetchFailedError):
        _client(handler).get_updates(offset=None, timeout_seconds=25)


def test_get_updates_raises_fetch_failed_on_malformed_json() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    with pytest.raises(TelegramFetchFailedError):
        _client(handler).get_updates(offset=None, timeout_seconds=25)


def test_get_updates_raises_fetch_failed_on_an_unexpected_shape() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": [{"unexpected": True}]})

    with pytest.raises(TelegramFetchFailedError):
        _client(handler).get_updates(offset=None, timeout_seconds=25)


def test_get_updates_raises_fetch_failed_on_a_transport_timeout() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    with pytest.raises(TelegramFetchFailedError):
        _client(handler).get_updates(offset=None, timeout_seconds=25)


def test_transport_error_messages_never_contain_the_token() -> None:
    """Regression for S11-F04: the raw httpx exception can echo the token-carrying request URL,
    so the error message must be token-free."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connect refused")

    client = HttpTelegramClient(
        api_token="SUPERSECRET_TOKEN",
        allowed_chat_ids=frozenset({1}),
        api_url="https://api.telegram.org",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(TelegramFetchFailedError) as get_error:
        client.get_updates(offset=None, timeout_seconds=25)
    with pytest.raises(TelegramFetchFailedError) as send_error:
        client.send_message(1, "hello")

    assert "SUPERSECRET_TOKEN" not in str(get_error.value)
    assert "SUPERSECRET_TOKEN" not in str(send_error.value)


def test_is_chat_allowed_checks_the_configured_allowlist() -> None:
    client = _client(lambda _request: httpx.Response(200, json={"ok": True, "result": []}))

    assert client.is_chat_allowed(1) is True
    assert client.is_chat_allowed(99) is False
