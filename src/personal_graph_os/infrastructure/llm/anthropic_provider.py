"""Anthropic HTTP provider adapter for Messages API (EP-2026-014 ST-01).

Supports Anthropic messages API using async httpx.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from personal_graph_os.application.llm_provider import LlmProviderRequestError


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
        payload = {
            "model": model or self._default_model,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_message},
            ],
            "max_tokens": 4096,
            "stream": False,
        }
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
                return "".join(
                    block.get("text", "")
                    for block in content_blocks
                    if isinstance(block, dict) and block.get("type") == "text"
                )
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
