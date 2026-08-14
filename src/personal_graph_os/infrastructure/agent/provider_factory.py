"""Agent-chat provider factory (EP-2026-012 Telegram agent bot follow-up).

Mirrors `infrastructure.enrichment/provider_factory.py`: opt-in and fail-closed. With
`PGOS_AGENT_CHAT_PROVIDER` unset or blank the factory returns `None` and the Telegram channel
keeps its plain URL-capture behavior; a recognized provider missing a required setting raises
instead of silently running unconfigured.
"""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from personal_graph_os.application.agent_adapters import AgentChatProviderError
from personal_graph_os.infrastructure.agent.chat_provider import (
    AgentChatProvider,
    AgentChatProviderConfig,
)

_HTTP_CHAT_PROVIDER_NAME = "http_chat"
_SUPPORTED_PROVIDER_NAMES = frozenset({_HTTP_CHAT_PROVIDER_NAME})

_ENV_PROVIDER = "PGOS_AGENT_CHAT_PROVIDER"
_ENV_BASE_URL = "PGOS_AGENT_CHAT_BASE_URL"
_ENV_API_KEY = "PGOS_AGENT_CHAT_API_KEY"
_ENV_MODEL = "PGOS_AGENT_CHAT_MODEL"
_ENV_REASONING_EFFORT = "PGOS_AGENT_CHAT_REASONING_EFFORT"


def build_agent_chat_provider_from_env(
    environ: Mapping[str, str], *, transport: httpx.BaseTransport | None = None
) -> AgentChatProvider | None:
    """Return the configured `AgentChatProvider`, or `None` if the agent chat is not configured
    at all (`PGOS_AGENT_CHAT_PROVIDER` unset or blank -- the fail-closed default)."""
    provider_name = (environ.get(_ENV_PROVIDER) or "").strip()
    if not provider_name:
        return None
    if provider_name not in _SUPPORTED_PROVIDER_NAMES:
        raise AgentChatProviderError(
            f"{_ENV_PROVIDER}={provider_name!r} is not a supported agent-chat provider "
            f"(supported: {sorted(_SUPPORTED_PROVIDER_NAMES)})"
        )

    base_url = (environ.get(_ENV_BASE_URL) or "").strip()
    api_key = (environ.get(_ENV_API_KEY) or "").strip()
    model_name = (environ.get(_ENV_MODEL) or "").strip()
    missing = [
        name
        for name, value in (
            (_ENV_BASE_URL, base_url),
            (_ENV_API_KEY, api_key),
            (_ENV_MODEL, model_name),
        )
        if not value
    ]
    if missing:
        raise AgentChatProviderError(
            f"{_ENV_PROVIDER}={provider_name!r} is missing required settings: {', '.join(missing)}"
        )

    reasoning_effort = (environ.get(_ENV_REASONING_EFFORT) or "").strip() or None
    return AgentChatProvider(
        AgentChatProviderConfig(
            base_url=base_url,
            api_key=api_key,
            model_name=model_name,
            reasoning_effort=reasoning_effort,
        ),
        transport=transport,
    )
