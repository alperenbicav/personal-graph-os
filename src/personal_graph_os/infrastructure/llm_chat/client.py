"""Shared POST + response-shape extraction for `/chat/completions` providers (ST-06).

Both the enrichment and the work-planning provider post one JSON request to an
OpenAI-compatible `/chat/completions` endpoint and must parse `choices[0].message.content`
as JSON. Only the error types differ (each feature keeps its own typed hierarchy), so this
helper takes error factories instead of raising anything of its own.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx


def complete_json_via_chat_completions(
    client: httpx.Client,
    *,
    base_url: str,
    request_body: dict[str, object],
    unavailable_error: Callable[[str], Exception],
    invalid_response_error: Callable[[str], Exception],
) -> Any:
    """Post `request_body`, return the parsed JSON object from the assistant message content.

    Transport/HTTP failures map through `unavailable_error`; a response whose shape is not the
    expected chat-completions envelope, or whose content is not JSON, maps through
    `invalid_response_error`.
    """
    try:
        response = client.post("/chat/completions", json=request_body)
        response.raise_for_status()
    except httpx.HTTPError as error:
        raise unavailable_error(
            f"chat completions request to {base_url!r} failed: {error}"
        ) from error

    try:
        message_content = response.json()["choices"][0]["message"]["content"]
        return json.loads(message_content)
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise invalid_response_error(
            f"chat completions response was not the expected shape: {error}"
        ) from error
