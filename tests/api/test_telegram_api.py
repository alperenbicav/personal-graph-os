from __future__ import annotations

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
