"""Offline `TelegramClient` for contract tests (EP-2026-012 ST-11): serves pre-seeded updates
and records outbound acks in memory, exactly like the real client's getUpdates/sendMessage, so
service/poller tests never touch the live Telegram API and never send anything anywhere real."""

from __future__ import annotations

from collections.abc import Sequence

from personal_graph_os.application.telegram_adapters import TelegramUpdate

_DEFAULT_ALLOWED_CHAT_IDS = frozenset({1})


class FakeTelegramClient:
    """`TelegramClient` backed by an in-memory update queue and ack outbox."""

    def __init__(
        self,
        updates: Sequence[TelegramUpdate] = (),
        *,
        allowed_chat_ids: frozenset[int] = _DEFAULT_ALLOWED_CHAT_IDS,
        outbox: list[tuple[int, str]] | None = None,
        file_bytes: dict[str, bytes] | None = None,
    ) -> None:
        self._updates = tuple(updates)
        self._allowed_chat_ids = allowed_chat_ids
        self._outbox = outbox if outbox is not None else []
        self._file_bytes = dict(file_bytes or {})

    @property
    def outbox(self) -> tuple[tuple[int, str], ...]:
        return tuple(self._outbox)

    def is_chat_allowed(self, chat_id: int) -> bool:
        return chat_id in self._allowed_chat_ids

    def get_updates(
        self, *, offset: int | None, timeout_seconds: int
    ) -> tuple[TelegramUpdate, ...]:
        if offset is None:
            return self._updates
        return tuple(update for update in self._updates if update.update_id >= offset)

    def send_message(self, chat_id: int, text: str) -> None:
        self._outbox.append((chat_id, text))

    def download_file(self, file_id: str) -> bytes:
        if file_id not in self._file_bytes:
            raise LookupError(f"no seeded bytes for file_id {file_id!r}")
        return self._file_bytes[file_id]
