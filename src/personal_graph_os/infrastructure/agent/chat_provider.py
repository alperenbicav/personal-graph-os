"""Tool-calling agent provider for the Telegram agent bot (EP-2026-012 follow-up).

Uses OpenAI's `/v1/responses` endpoint (not `/chat/completions`): gpt-5.6-luna rejects function
tools combined with `reasoning_effort` on chat/completions, while `/responses` accepts
`reasoning: {"effort": ...}` alongside tools. Same `base_url`/`api_key`/`model_name` config
shape as the enrichment/planning providers, and the `api_key` is never logged or surfaced
outside the `Authorization` header.

The Responses API requires each prior `function_call` to be re-sent with its paired `reasoning`
item, so `AgentChatTurn.output_items` carries the raw output array for the loop to echo back.

No live call is wired by this module: constructing `AgentChatProvider` requires an explicit
`base_url`/`api_key`, and every test drives it through `httpx.MockTransport`. Paid live execution
is separate, bounded, and recorded in WORK.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

from personal_graph_os.application.agent_adapters import (
    AgentChatProviderError,
    AgentChatTurn,
    AgentTool,
    AgentToolCall,
)


@dataclass(frozen=True)
class AgentChatProviderConfig:
    """Everything needed to reach one configured chat-completions endpoint. `api_key` is never
    logged, persisted, or otherwise surfaced outside the `Authorization` request header."""

    base_url: str
    api_key: str
    model_name: str
    timeout_seconds: float = 30.0
    reasoning_effort: str | None = None


class AgentChatProvider:
    """A minimal tool-calling client over an OpenAI-compatible `/chat/completions` endpoint.

    The caller owns the conversation: it passes the full `messages` history (including any prior
    `assistant` tool_calls and `tool` results) and receives one turn at a time.
    """

    def __init__(
        self,
        config: AgentChatProviderConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = config
        self._client = httpx.Client(
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            headers={"Authorization": f"Bearer {config.api_key}"},
            transport=transport,
        )

    @property
    def name(self) -> str:
        return f"agent-chat:{self._config.model_name}"

    def complete(
        self,
        *,
        input_items: list[dict[str, object]],
        tools: tuple[AgentTool, ...] = (),
    ) -> AgentChatTurn:
        request_body: dict[str, object] = {
            "model": self._config.model_name,
            "input": input_items,
        }
        if tools:
            # Responses-API tool schema is flat (`type`/`name`/`description`/`parameters`).
            request_body["tools"] = [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                }
                for tool in tools
            ]
        if self._config.reasoning_effort is not None:
            request_body["reasoning"] = {"effort": self._config.reasoning_effort}

        try:
            response = self._client.post("/responses", json=request_body)
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise AgentChatProviderError(
                f"agent chat completion request to {self._config.base_url!r} failed: {error}"
            ) from error

        try:
            output_items = response.json()["output"]
        except (KeyError, TypeError, ValueError) as error:
            raise AgentChatProviderError(
                f"agent chat completion response was not the expected shape: {error}"
            ) from error
        if not isinstance(output_items, list):
            raise AgentChatProviderError("agent chat completion response output was not a list")

        final_text: str | None = None
        tool_calls: list[AgentToolCall] = []
        for item in output_items:
            item_type = item.get("type")
            if item_type == "message":
                content = item.get("content")
                if isinstance(content, list):
                    text = "".join(
                        part.get("text", "")
                        for part in content
                        if isinstance(part, dict) and part.get("type") == "output_text"
                    )
                    if text.strip():
                        final_text = text
            elif item_type == "function_call":
                name = item.get("name")
                if not isinstance(name, str) or not name:
                    continue
                try:
                    arguments = json.loads(item.get("arguments") or "{}")
                except ValueError:
                    arguments = {}
                if not isinstance(arguments, dict):
                    arguments = {}
                tool_calls.append(
                    AgentToolCall(
                        call_id=str(item.get("call_id") or item.get("id") or ""),
                        name=name,
                        arguments=arguments,
                    )
                )
        return AgentChatTurn(
            final_text=final_text,
            tool_calls=tuple(tool_calls),
            output_items=tuple(output_items),
        )
