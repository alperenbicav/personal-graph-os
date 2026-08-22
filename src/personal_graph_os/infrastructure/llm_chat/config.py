"""The one config shape shared by every OpenAI-style chat provider (ST-06)."""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class ChatCompletionsProviderConfig:
    """Everything needed to reach one configured chat endpoint. `api_key` is never logged,
    persisted, or otherwise surfaced outside the `Authorization` request header.
    `reasoning_effort` (OpenAI `reasoning_effort` request field, e.g. `high`) is passed through
    when set and omitted otherwise, so a reasoning-capable model can be tuned without changing
    callers."""

    base_url: str
    api_key: str
    model_name: str
    timeout_seconds: float = 30.0
    reasoning_effort: str | None = None


def open_chat_completions_client(
    config: ChatCompletionsProviderConfig,
    *,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    """Build the bearer-authenticated client every provider posts through."""
    return httpx.Client(
        base_url=config.base_url,
        timeout=config.timeout_seconds,
        headers={"Authorization": f"Bearer {config.api_key}"},
        transport=transport,
    )
