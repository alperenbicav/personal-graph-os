"""Provider factory for multi-provider LLM support (EP-2026-014 ST-01).

Configures OpenAI, OpenRouter, or Anthropic based on PGOS_LLM_PROVIDER,
PGOS_LLM_BASE_URL, PGOS_LLM_API_KEY, PGOS_LLM_MODEL.
"""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from personal_graph_os.application.llm_provider import LlmProvider, LlmProviderError
from personal_graph_os.infrastructure.llm.anthropic_provider import AnthropicLlmProvider
from personal_graph_os.infrastructure.llm.openai_provider import OpenAiLlmProvider

_DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "anthropic": "https://api.anthropic.com/v1",
}

_DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "openrouter": "openai/gpt-4o-mini",
    "anthropic": "claude-3-5-sonnet-20241022",
}


def build_llm_provider_from_env(
    environ: Mapping[str, str],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> LlmProvider | None:
    """Return configured , or  if unconfigured."""
    api_key = environ.get("PGOS_LLM_API_KEY", "").strip()
    if not api_key:
        return None

    provider_name = environ.get("PGOS_LLM_PROVIDER", "openai").strip().lower() or "openai"
    if provider_name not in _DEFAULT_BASE_URLS:
        raise LlmProviderError(f"Unsupported PGOS_LLM_PROVIDER: {provider_name!r}")

    base_url = environ.get("PGOS_LLM_BASE_URL", "").strip() or _DEFAULT_BASE_URLS[provider_name]
    default_model = environ.get("PGOS_LLM_MODEL", "").strip() or _DEFAULT_MODELS[provider_name]

    if provider_name in ("openai", "openrouter"):
        return OpenAiLlmProvider(
            base_url=base_url,
            api_key=api_key,
            default_model=default_model,
            transport=transport,
        )
    elif provider_name == "anthropic":
        return AnthropicLlmProvider(
            base_url=base_url,
            api_key=api_key,
            default_model=default_model,
            transport=transport,
        )

    return None
