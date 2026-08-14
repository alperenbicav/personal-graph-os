"""Agent chat adapter boundary (EP-2026-012 Telegram agent bot follow-up).

`application.telegram_service` depends only on the `GraphAgent` protocol; the concrete bounded
`AgentLoopService` lives in `infrastructure` and is injected at the composition root, keeping the
application layer infrastructure-free (mirrors `WorkPlanningService`/`EnrichmentService` behind
`application/*_adapters.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class AgentChatProviderError(Exception):
    """Raised when a configured agent chat provider could not produce a turn -- a transport
    failure, a rate limit, or an unusable response. Never a partial or best-effort result."""


@dataclass(frozen=True)
class AgentTool:
    """An OpenAI function-calling tool the bot's loop may offer to the model."""

    name: str
    description: str
    parameters: dict[str, object]


@dataclass(frozen=True)
class AgentToolCall:
    """One function call the model requested: `arguments` is already parsed JSON."""

    call_id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class AgentChatTurn:
    """One model response: either a final message, or one or more tool calls, or both.

    `output_items` carries the provider's raw output items (including the model's `reasoning`
    item) so the loop can echo them back into the next request -- the Responses API requires
    each `function_call` to be re-sent alongside its paired `reasoning` item.
    """

    final_text: str | None
    tool_calls: tuple[AgentToolCall, ...] = ()
    output_items: tuple[dict[str, object], ...] = ()


class AgentChatProvider(Protocol):
    """A tool-calling chat-completions client the agent loop drives one turn at a time.

    The caller owns the conversation history; the provider only renders one model response.
    Never raises anything other than `AgentChatProviderError` on failure.
    """

    @property
    def name(self) -> str:
        """Stable provider identity (e.g. `"agent-chat:<model>"`)."""
        ...

    def complete(
        self,
        *,
        input_items: list[dict[str, object]],
        tools: tuple[AgentTool, ...] = (),
    ) -> AgentChatTurn:
        """Return one model turn (final text and/or tool calls)."""
        ...


class GraphAgent(Protocol):
    """The conversational graph agent the Telegram channel may use for free-form messages.

    `application.telegram_service` depends only on this protocol; the concrete bounded
    `AgentLoopService` lives in `infrastructure` and is injected at the composition root.
    """

    @property
    def provider_name(self) -> str:
        """Stable identity of the underlying chat provider."""
        ...

    def run(
        self,
        *,
        user_message: str,
        actor_name: str,
        request_id: str,
        attachment_image_data_url: str | None = None,
    ) -> str:
        """Answer `user_message`, optionally mutating the graph (attributed), bounded. When
        `attachment_image_data_url` is set (a base64 data URL of a screenshot), the model also
        sees the image (vision)."""
        ...


__all__ = [
    "AgentChatProvider",
    "AgentChatProviderError",
    "AgentChatTurn",
    "AgentTool",
    "AgentToolCall",
    "GraphAgent",
]
