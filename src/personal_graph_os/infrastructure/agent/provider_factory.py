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
from personal_graph_os.infrastructure.llm_chat import (
    LlmChatSettingsError,
    parse_provider_settings,
)

_HTTP_CHAT_PROVIDER_NAME = "http_chat"
_SUPPORTED_PROVIDER_NAMES = frozenset({_HTTP_CHAT_PROVIDER_NAME})


def build_agent_chat_provider_from_env(
    environ: Mapping[str, str], *, transport: httpx.BaseTransport | None = None
) -> AgentChatProvider | None:
    """Return the configured `AgentChatProvider`, or `None` if the agent chat is not configured
    at all (`PGOS_AGENT_CHAT_PROVIDER` unset or blank -- the fail-closed default)."""
    try:
        settings = parse_provider_settings(
            environ, env_prefix="AGENT_CHAT", supported_provider_names=_SUPPORTED_PROVIDER_NAMES
        )
    except LlmChatSettingsError as error:
        if error.unsupported:
            raise AgentChatProviderError(
                f"{error.provider_env}={error.provider_value!r} is not a supported agent-chat "
                f"provider (supported: {sorted(error.supported)})"
            ) from error
        raise AgentChatProviderError(
            f"{error.provider_env}={error.provider_value!r} is missing required settings: "
            f"{', '.join(error.missing)}"
        ) from error

    if settings is None:
        return None
    return AgentChatProvider(
        AgentChatProviderConfig(
            base_url=settings.base_url,
            api_key=settings.api_key,
            model_name=settings.model_name,
            reasoning_effort=settings.reasoning_effort,
        ),
        transport=transport,
    )
