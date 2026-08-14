from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import httpx
import pytest

from personal_graph_os.api.app import create_app


def test_telegram_poller_is_wired_only_when_telegram_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PGOS_TELEGRAM_ENABLED", "1")
    monkeypatch.setenv("PGOS_TELEGRAM_API_TOKEN", "test-token")
    monkeypatch.setenv("PGOS_TELEGRAM_ALLOWED_CHAT_IDS", "1")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": []})

    app = create_app(
        tmp_path / "telegram-workspace.db",
        telegram_transport=httpx.MockTransport(handler),
    )

    assert app.state.telegram_service is not None
    assert app.state.telegram_poller is not None


def test_telegram_poller_stays_disabled_when_unconfigured(tmp_path: Path) -> None:
    app = create_app(tmp_path / "no-telegram-workspace.db")

    assert app.state.telegram_poller is None


def test_the_poller_captures_a_telegram_message_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance 1/3/4: with the app running, an update from an allowlisted chat is captured via
    the same pipeline as MCP capture and the cursor advances; the poller stops on shutdown."""
    monkeypatch.setenv("PGOS_TELEGRAM_ENABLED", "1")
    monkeypatch.setenv("PGOS_TELEGRAM_API_TOKEN", "test-token")
    monkeypatch.setenv("PGOS_TELEGRAM_ALLOWED_CHAT_IDS", "1")

    update_payload = {
        "update_id": 7,
        "message": {
            "message_id": 7,
            "date": 1720000000,
            "chat": {"id": 1},
            "from": {"id": 99, "is_bot": False},
            "text": "https://example.com/article",
        },
    }
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if request.url.path.endswith("/getUpdates"):
            result = [update_payload] if calls["count"] == 1 else []
            return httpx.Response(200, json={"ok": True, "result": result})
        return httpx.Response(200, json={"ok": True, "result": {}})

    database_path = tmp_path / "telegram-e2e.db"
    app = create_app(
        database_path,
        telegram_transport=httpx.MockTransport(handler),
    )
    from fastapi.testclient import TestClient

    with TestClient(app) as _client:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            connection = sqlite3.connect(database_path)
            row = connection.execute(
                "SELECT source_identifier FROM ingestion_jobs WHERE source = 'telegram'"
            ).fetchone()
            connection.close()
            if row is not None:
                break
            time.sleep(0.1)

    assert row is not None
    assert row[0] == "7"

    connection = sqlite3.connect(database_path)
    cursor = connection.execute(
        "SELECT cursor_value FROM channel_sync_state WHERE channel = 'telegram'"
    ).fetchone()
    connection.close()
    assert cursor is not None
    assert cursor[0] == "00000000000000000007"


def test_the_poller_answers_a_non_url_message_via_the_agent_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With an agent chat provider configured, a free-form message is answered by the bounded
    graph agent (tool-calling loop) instead of the 'send a URL' fallback."""
    monkeypatch.setenv("PGOS_TELEGRAM_ENABLED", "1")
    monkeypatch.setenv("PGOS_TELEGRAM_API_TOKEN", "test-token")
    monkeypatch.setenv("PGOS_TELEGRAM_ALLOWED_CHAT_IDS", "1")
    monkeypatch.setenv("PGOS_AGENT_CHAT_PROVIDER", "http_chat")
    monkeypatch.setenv("PGOS_AGENT_CHAT_BASE_URL", "https://agent.example/v1")
    monkeypatch.setenv("PGOS_AGENT_CHAT_API_KEY", "agent-key")
    monkeypatch.setenv("PGOS_AGENT_CHAT_MODEL", "gpt-5.6-luna")

    update_payload = {
        "update_id": 9,
        "message": {
            "message_id": 9,
            "date": 1720000000,
            "chat": {"id": 1},
            "from": {"id": 99, "is_bot": False},
            "text": "what is stored in my graph?",
        },
    }
    sent: list[dict[str, object]] = []
    calls = {"count": 0}

    def telegram_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            calls["count"] += 1
            result = [update_payload] if calls["count"] == 1 else []
            return httpx.Response(200, json={"ok": True, "result": result})
        if request.url.path.endswith("/sendMessage"):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True, "result": {}})
        return httpx.Response(200, json={"ok": True, "result": {}})

    def agent_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "You have 2 papers stored."}],
                    }
                ]
            },
        )

    database_path = tmp_path / "telegram-agent.db"
    app = create_app(
        database_path,
        telegram_transport=httpx.MockTransport(telegram_handler),
        agent_chat_transport=httpx.MockTransport(agent_handler),
    )
    from fastapi.testclient import TestClient

    with TestClient(app) as _client:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            connection = sqlite3.connect(database_path)
            cursor = connection.execute(
                "SELECT cursor_value FROM channel_sync_state WHERE channel = 'telegram'"
            ).fetchone()
            connection.close()
            if cursor is not None and cursor[0] == "00000000000000000009":
                break
            time.sleep(0.1)

    assert cursor is not None
    assert cursor[0] == "00000000000000000009"
    assert sent, "the bot should have replied to the free-form message"
    assert "2 papers stored" in str(sent[0].get("text"))
    assert sent[0].get("chat_id") == 1


def test_the_agent_captures_a_url_with_free_form_intent_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A URL plus natural-language intent text is handled by the agent (which captures the URL
    and replies) instead of the bounded-vocabulary clarification."""
    monkeypatch.setenv("PGOS_TELEGRAM_ENABLED", "1")
    monkeypatch.setenv("PGOS_TELEGRAM_API_TOKEN", "test-token")
    monkeypatch.setenv("PGOS_TELEGRAM_ALLOWED_CHAT_IDS", "1")
    monkeypatch.setenv("PGOS_AGENT_CHAT_PROVIDER", "http_chat")
    monkeypatch.setenv("PGOS_AGENT_CHAT_BASE_URL", "https://agent.example/v1")
    monkeypatch.setenv("PGOS_AGENT_CHAT_API_KEY", "agent-key")
    monkeypatch.setenv("PGOS_AGENT_CHAT_MODEL", "gpt-5.6-luna")

    update_payload = {
        "update_id": 13,
        "message": {
            "message_id": 13,
            "date": 1720000000,
            "chat": {"id": 1},
            "from": {"id": 99, "is_bot": False},
            "text": "okay, https://arxiv.org/abs/2607.18261 bu paper'ı incele ve özetle",
        },
    }
    sent: list[dict[str, object]] = []
    calls = {"count": 0}
    agent_calls = {"count": 0}

    def telegram_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            calls["count"] += 1
            result = [update_payload] if calls["count"] == 1 else []
            return httpx.Response(200, json={"ok": True, "result": result})
        if request.url.path.endswith("/sendMessage"):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True, "result": {}})
        return httpx.Response(200, json={"ok": True, "result": {}})

    def agent_handler(request: httpx.Request) -> httpx.Response:
        agent_calls["count"] += 1
        if agent_calls["count"] == 1:
            return httpx.Response(
                200,
                json={
                    "output": [
                        {
                            "type": "function_call",
                            "id": "fc_1",
                            "call_id": "call_1",
                            "name": "capture_url",
                            "arguments": json.dumps({"url": "https://arxiv.org/abs/2607.18261"}),
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": "Saved and summarized the paper."}
                        ],
                    }
                ]
            },
        )

    database_path = tmp_path / "telegram-url-intent.db"
    app = create_app(
        database_path,
        telegram_transport=httpx.MockTransport(telegram_handler),
        agent_chat_transport=httpx.MockTransport(agent_handler),
    )
    from fastapi.testclient import TestClient

    with TestClient(app) as _client:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            connection = sqlite3.connect(database_path)
            cursor = connection.execute(
                "SELECT cursor_value FROM channel_sync_state WHERE channel = 'telegram'"
            ).fetchone()
            connection.close()
            if cursor is not None and cursor[0] == "00000000000000000013":
                break
            time.sleep(0.1)

    assert cursor is not None
    assert cursor[0] == "00000000000000000013"
    assert sent, "the bot should have replied instead of clarifying"
    assert "Saved and summarized" in str(sent[0].get("text"))
    connection = sqlite3.connect(database_path)
    job = connection.execute(
        "SELECT source_identifier FROM ingestion_jobs WHERE source = 'telegram'"
    ).fetchone()
    connection.close()
    assert job is not None and job[0] == "tg-13"


def test_the_agent_bot_is_fail_closed_without_a_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without PGOS_AGENT_CHAT_PROVIDER the channel keeps the 'send a URL' fallback for a
    non-URL message."""
    monkeypatch.setenv("PGOS_TELEGRAM_ENABLED", "1")
    monkeypatch.setenv("PGOS_TELEGRAM_API_TOKEN", "test-token")
    monkeypatch.setenv("PGOS_TELEGRAM_ALLOWED_CHAT_IDS", "1")

    update_payload = {
        "update_id": 11,
        "message": {
            "message_id": 11,
            "date": 1720000000,
            "chat": {"id": 1},
            "from": {"id": 99, "is_bot": False},
            "text": "hello",
        },
    }
    sent: list[dict[str, object]] = []
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            calls["count"] += 1
            result = [update_payload] if calls["count"] == 1 else []
            return httpx.Response(200, json={"ok": True, "result": result})
        if request.url.path.endswith("/sendMessage"):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True, "result": {}})
        return httpx.Response(200, json={"ok": True, "result": {}})

    database_path = tmp_path / "telegram-failclosed.db"
    app = create_app(database_path, telegram_transport=httpx.MockTransport(handler))
    from fastapi.testclient import TestClient

    with TestClient(app) as _client:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            connection = sqlite3.connect(database_path)
            cursor = connection.execute(
                "SELECT cursor_value FROM channel_sync_state WHERE channel = 'telegram'"
            ).fetchone()
            connection.close()
            if cursor is not None and cursor[0] == "00000000000000000011":
                break
            time.sleep(0.1)

    assert cursor is not None
    assert cursor[0] == "00000000000000000011"
    assert sent
    assert "could not find a link" in str(sent[0].get("text"))
