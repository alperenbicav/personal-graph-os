"""Telegram channel-adapter contract: the only boundary `TelegramService` and `TelegramPoller`
depend on (EP-2026-012 ST-11), mirroring `application/clickup_adapters.py`.

A concrete real client and an offline fake both live in `infrastructure/telegram/`; application
code never imports them. The DTOs are the typed, normalized read model of one Bot API update --
already freed of Telegram's raw JSON shape and the reserved `from` field name -- and
`TelegramClient` is the narrow transport surface: long-poll `get_updates`, `send_message` for
acks, and the configured chat-allowlist check that gates every inbound message.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from personal_graph_os.domain.errors import DomainError


class TelegramError(DomainError):
    """Base type for every Telegram channel failure surfaced to the application layer."""


class TelegramNotConfiguredError(TelegramError):
    """Raised when Telegram processing is attempted but no `TelegramClient` is wired
    (`PGOS_TELEGRAM_ENABLED`/token/allowlist missing): fails closed, never runs unconfigured."""


class TelegramAccessDeniedError(TelegramError):
    """Raised when the Bot API rejects the configured token (401/403)."""


class TelegramRateLimitedError(TelegramError):
    """Raised when the Bot API returns 429 -- the poller backs off and retries."""


class TelegramFetchFailedError(TelegramError):
    """Raised when the Bot API could not be reached or returned an unusable response
    (network/timeout, an unexpected non-2xx, or `ok:false` in the body)."""


class TelegramChat(BaseModel):
    id: int


class TelegramUser(BaseModel):
    id: int
    is_bot: bool = False
    username: str | None = None


class TelegramMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    message_id: int
    date: int
    chat: TelegramChat
    from_: TelegramUser | None = Field(default=None, alias="from")
    text: str | None = None
    caption: str | None = None

    @field_validator("text", "caption")
    @classmethod
    def _strip_blank_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @property
    def content(self) -> str | None:
        """The message's text, falling back to a media `caption` (S11-F02): a photo/document with
        a caption carrying a URL is capturable exactly like a plain-text message."""
        return self.text or self.caption


class TelegramUpdate(BaseModel):
    update_id: int
    message: TelegramMessage | None = None


class TelegramClient(Protocol):
    """Reads Telegram updates and sends acks; never does anything else.

    Raises `TelegramAccessDeniedError` for a rejected token, `TelegramRateLimitedError` on 429,
    and `TelegramFetchFailedError` for any transport/unusable-response failure. `is_chat_allowed`
    is the configured allowlist check the service applies to every inbound message before it can
    trigger any capture work.
    """

    def get_updates(
        self, *, offset: int | None, timeout_seconds: int
    ) -> tuple[TelegramUpdate, ...]: ...
    def send_message(self, chat_id: int, text: str) -> None: ...
    def is_chat_allowed(self, chat_id: int) -> bool: ...


__all__ = [
    "TelegramAccessDeniedError",
    "TelegramChat",
    "TelegramClient",
    "TelegramError",
    "TelegramFetchFailedError",
    "TelegramMessage",
    "TelegramNotConfiguredError",
    "TelegramRateLimitedError",
    "TelegramUpdate",
    "TelegramUser",
]
