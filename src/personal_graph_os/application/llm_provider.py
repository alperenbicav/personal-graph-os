"""Application boundary for LLM chat and streaming generation (EP-2026-014 ST-01).

Services depend on  protocol; concrete HTTP adapters (OpenAI, Anthropic, OpenRouter)
live in .
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol


class LlmProviderError(Exception):
    """Base exception for LLM provider errors."""


class LlmNotConfiguredError(LlmProviderError):
    """Raised when an LLM call is attempted but the provider is not configured."""


class LlmProviderRequestError(LlmProviderError):
    """Raised when an HTTP or upstream provider API call fails."""


class LlmProvider(Protocol):
    """Protocol for LLM completions and streaming responses."""

    async def complete(
        self,
        *,
        system_prompt: str,
        user_message: str,
        model: str | None = None,
    ) -> str:
        """Generate a complete non-streaming assistant reply."""
        ...

    async def stream(
        self,
        *,
        system_prompt: str,
        user_message: str,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream incremental assistant text chunks."""
        ...
