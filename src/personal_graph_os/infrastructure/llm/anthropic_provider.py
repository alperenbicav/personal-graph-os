"""Anthropic HTTP provider adapter for Messages API (EP-2026-014 ST-01).

Supports Anthropic messages API using async httpx.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from personal_graph_os.application.llm_provider import (
    LlmProviderRequestError,
    LlmResponse,
    LlmToolCall,
)


class AnthropicLlmProvider:
    """Anthropic Messages API client."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        default_model: str,
        timeout_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._default_model = default_model
        self._timeout = timeout_seconds
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    async def complete(
        self,
        *,
        system_prompt: str,
        user_message: str,
        model: str | None = None,
    ) -> str:
        res = await self.chat_turn(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            model=model,
        )
        return res.content or ""

    async def chat_turn(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
    ) -> LlmResponse:
        system_content = ""
        anthropic_messages: list[dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            if role == "system":
                system_content = str(content or "")
            elif role == "user":
                anthropic_messages.append({"role": "user", "content": str(content or "")})
            elif role == "assistant":
                blocks: list[dict[str, Any]] = []
                if content:
                    blocks.append({"type": "text", "text": str(content)})
                for tc in msg.get("tool_calls", []):
                    fn = tc.get("function", {})
                    fn_name = fn.get("name", "")
                    raw_args = fn.get("arguments", "{}")
                    try:
                        args = (
                            json.loads(raw_args)
                            if isinstance(raw_args, str)
                            else (raw_args or {})
                        )
                    except Exception:
                        args = {}
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.get("id", ""),
                            "name": fn_name,
                            "input": args,
                        }
                    )
                anthropic_messages.append(
                    {"role": "assistant", "content": blocks if blocks else str(content or "")}
                )
            elif role == "tool":
                anthropic_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.get("tool_call_id", ""),
                                "content": str(content or ""),
                            }
                        ],
                    }
                )

        payload: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": anthropic_messages,
            "max_tokens": 4096,
            "stream": False,
        }
        if system_content:
            payload["system"] = system_content
        if tools:
            payload["tools"] = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"]["description"],
                    "input_schema": t["function"]["parameters"],
                }
                for t in tools
                if "function" in t
            ]

        url = f"{self._base_url}/messages"
        try:
            async with httpx.AsyncClient(
                headers=self._headers(),
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                content_blocks = data.get("content", [])
                text_parts: list[str] = []
                tool_calls: list[LlmToolCall] = []

                for block in content_blocks:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            tool_calls.append(
                                LlmToolCall(
                                    id=block.get("id", ""),
                                    name=block.get("name", ""),
                                    arguments=block.get("input", {}),
                                )
                            )

                final_text = "".join(text_parts) if text_parts else None
                return LlmResponse(content=final_text, tool_calls=tool_calls)
        except httpx.HTTPError as error:
            raise LlmProviderRequestError(
                f"Anthropic messages request failed: {error}"
            ) from error
        except (KeyError, IndexError, TypeError) as error:
            raise LlmProviderRequestError(
                f"Unexpected response shape from Anthropic provider: {error}"
            ) from error

    async def stream(
        self,
        *,
        system_prompt: str,
        user_message: str,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        payload = {
            "model": model or self._default_model,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_message},
            ],
            "max_tokens": 4096,
            "stream": True,
        }
        url = f"{self._base_url}/messages"
        try:
            async with httpx.AsyncClient(
                headers=self._headers(),
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                async with client.stream("POST", url, json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith("data: "):
                            raw = line[6:].strip()
                            if raw == "[DONE]":
                                break
                            try:
                                data = json.loads(raw)
                                event_type = data.get("type")
                                if event_type == "content_block_delta":
                                    text = data.get("delta", {}).get("text", "")
                                    if text:
                                        yield text
                            except json.JSONDecodeError:
                                continue
        except httpx.HTTPError as error:
            raise LlmProviderRequestError(f"Anthropic streaming request failed: {error}") from error
