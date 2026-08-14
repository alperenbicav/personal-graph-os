"""Bounded Telegram long-poll driver (EP-2026-012 ST-11).

`TelegramPoller.poll_once()` is the testable unit: it resumes from the stored cursor, fetches the
next batch of updates, and lets `TelegramService` handle each one in order (stopping at the first
failure so the cursor never skips a failed update). `run()` is the thin production loop with a
`should_stop` hook and backoff on Telegram errors/rate limits.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from personal_graph_os.application.telegram_adapters import (
    TelegramClient,
    TelegramError,
    TelegramRateLimitedError,
)
from personal_graph_os.application.telegram_service import TelegramService

_DEFAULT_POLL_TIMEOUT_SECONDS = 25
_DEFAULT_BACKOFF_SECONDS = 3.0
_DEFAULT_IDLE_SLEEP_SECONDS = 0.2


class TelegramPoller:
    def __init__(
        self,
        telegram_client: TelegramClient,
        telegram_service: TelegramService,
        *,
        poll_timeout_seconds: int = _DEFAULT_POLL_TIMEOUT_SECONDS,
        backoff_seconds: float = _DEFAULT_BACKOFF_SECONDS,
        idle_sleep_seconds: float = _DEFAULT_IDLE_SLEEP_SECONDS,
    ) -> None:
        self._telegram_client = telegram_client
        self._telegram_service = telegram_service
        self._poll_timeout_seconds = poll_timeout_seconds
        self._backoff_seconds = backoff_seconds
        self._idle_sleep_seconds = idle_sleep_seconds

    def poll_once(self) -> int:
        """Fetch one batch starting at the resumed cursor and handle each update in order.

        Returns how many updates were handled. A Telegram failure mid-batch propagates so the
        caller can back off; already-handled updates keep their advanced cursor, the failed one
        stays pending for the next poll.
        """
        offset = self._telegram_service.current_offset()
        updates = self._telegram_client.get_updates(
            offset=offset, timeout_seconds=self._poll_timeout_seconds
        )
        handled = 0
        for update in updates:
            self._telegram_service.process_update(update)
            handled += 1
        return handled

    def run(self, should_stop: Callable[[], bool]) -> None:
        """Poll until `should_stop()` returns True, backing off on Telegram failures/rate limits.

        Runs in the caller's thread (the app wires it onto a daemon thread in the lifespan).
        """
        while not should_stop():
            try:
                self.poll_once()
            except (TelegramRateLimitedError, TelegramError):
                time.sleep(self._backoff_seconds)
            else:
                time.sleep(self._idle_sleep_seconds)
