"""Application boundary for LLM chat and streaming generation (EP-2026-014 ST-01).

Services depend on  protocol; concrete HTTP adapters (OpenAI, Anthropic, OpenRouter)
live in .
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol


class LlmProviderError(Exception):
    """Base exception for LLM provider errors."""


class LlmNotConfiguredError(LlmProviderError):
    """Raised when an LLM call is attempted but the provider is not configured."""


class LlmProviderRequestError(LlmProviderError):
    """Raised when an HTTP or upstream provider API call fails."""


@dataclass(frozen=True)
class LlmToolCall:
    """Represents a tool call emitted by an LLM."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class LlmResponse:
    """Represents one model completion turn, containing text and/or tool calls."""

    content: str | None
    tool_calls: list[LlmToolCall] = field(default_factory=list)


class LlmProvider(Protocol):
    """Protocol for LLM completions, tool-calling turns, and streaming responses."""

    async def complete(
        self,
        *,
        system_prompt: str,
        user_message: str,
        model: str | None = None,
    ) -> str:
        """Generate a complete non-streaming assistant reply."""
        ...

    def stream(
        self,
        *,
        system_prompt: str,
        user_message: str,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream incremental assistant text chunks."""
        ...

    async def chat_turn(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
    ) -> LlmResponse:
        """Run a single conversational turn with optional OpenAI-style tools."""
        ...
