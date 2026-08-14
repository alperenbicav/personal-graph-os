"""Fail-closed runtime composition for the configured `TelegramClient` (EP-2026-012 ST-11),
mirroring `infrastructure.clickup.builder`.

Telegram is opt-in and fail-closed: with `PGOS_TELEGRAM_ENABLED` unset/not-true, no token set, or
no chat allowlist configured, this returns `None` and no poller ever starts -- an unconfigured
deployment makes zero Telegram calls. The allowlist is required (never optional) so an enabled
bot is never open to arbitrary Telegram chats. The token is consumed only by the client instance;
it is never stored in the database, written to logs, or exposed through any response/export.

Lives under `infrastructure/`, not `application/`, because it imports the concrete
`HttpTelegramClient` (dependency direction: application depends on nothing concrete).
"""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from personal_graph_os.application.telegram_adapters import TelegramClient
from personal_graph_os.infrastructure.telegram.telegram_client import (
    DEFAULT_TELEGRAM_API_URL,
    HttpTelegramClient,
)

_ENV_ENABLED = "PGOS_TELEGRAM_ENABLED"
_ENV_API_TOKEN = "PGOS_TELEGRAM_API_TOKEN"
_ENV_ALLOWED_CHAT_IDS = "PGOS_TELEGRAM_ALLOWED_CHAT_IDS"
_ENV_API_URL = "PGOS_TELEGRAM_API_URL"

_ENABLED_VALUES = frozenset({"1", "true", "yes", "on"})


def build_telegram_client_from_env(
    environ: Mapping[str, str], *, transport: httpx.BaseTransport | None = None
) -> TelegramClient | None:
    """Return the configured `TelegramClient`, or `None` when Telegram is not enabled/configured
    at all (the default, fully local posture with no Telegram integration and no external calls).

    Fails closed on any of: `PGOS_TELEGRAM_ENABLED` unset or not a truthy value (the kill
    switch), `PGOS_TELEGRAM_API_TOKEN` missing/blank, or `PGOS_TELEGRAM_ALLOWED_CHAT_IDS` empty.

    `transport` is a testability seam only; real callers never pass it.
    """
    enabled = (environ.get(_ENV_ENABLED) or "").strip().lower()
    if enabled not in _ENABLED_VALUES:
        return None
    api_token = (environ.get(_ENV_API_TOKEN) or "").strip()
    if not api_token:
        return None
    allowed_raw = (environ.get(_ENV_ALLOWED_CHAT_IDS) or "").strip()
    allowed_chat_ids = frozenset(
        int(item.strip()) for item in allowed_raw.split(",") if item.strip()
    )
    if not allowed_chat_ids:
        return None
    api_url = (environ.get(_ENV_API_URL) or "").strip() or DEFAULT_TELEGRAM_API_URL
    return HttpTelegramClient(
        api_token=api_token,
        allowed_chat_ids=allowed_chat_ids,
        api_url=api_url,
        transport=transport,
    )
