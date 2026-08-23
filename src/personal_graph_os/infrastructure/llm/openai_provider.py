"""OpenAI-compatible HTTP provider adapter for chat completions (EP-2026-014 ST-01).

Supports standard OpenAI and OpenRouter endpoints using async httpx.
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


class OpenAiLlmProvider:
    """OpenAI / OpenRouter chat-completions client."""

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
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
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
        payload: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools

        url = f"{self._base_url}/chat/completions"
        try:
            async with httpx.AsyncClient(
                headers=self._headers(),
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                choice = data["choices"][0]
                message = choice.get("message", {})
                content = message.get("content")
                raw_tool_calls = message.get("tool_calls") or []
                tool_calls: list[LlmToolCall] = []
                for tc in raw_tool_calls:
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
                    tool_calls.append(
                        LlmToolCall(
                            id=tc.get("id", ""),
                            name=fn_name,
                            arguments=args,
                        )
                    )

                return LlmResponse(content=content, tool_calls=tool_calls)
        except httpx.HTTPError as error:
            raise LlmProviderRequestError(
                f"OpenAI chat completions request failed: {error}"
            ) from error
        except (KeyError, IndexError, TypeError) as error:
            raise LlmProviderRequestError(
                f"Unexpected response shape from OpenAI provider: {error}"
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
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "stream": True,
        }
        url = f"{self._base_url}/chat/completions"
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
                                choices = data.get("choices", [{}])
                                delta = choices[0].get("delta", {}).get("content", "")
                                if delta:
                                    yield delta
                            except json.JSONDecodeError:
                                continue
        except httpx.HTTPError as error:
            raise LlmProviderRequestError(
                f"OpenAI chat streaming request failed: {error}"
            ) from error
