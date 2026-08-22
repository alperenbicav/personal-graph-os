"""Shared plumbing for OpenAI-style LLM chat providers (EP-2026-012 ST-06).

The enrichment, work-planning, and Telegram agent-chat providers previously carried three
near-identical copies of the same config dataclass, httpx client construction, and
environment-variable parsing. This package is the single home for those pieces; each feature
provider keeps only what is genuinely its own: its prompt, its response-to-domain mapping, its
typed error hierarchy, and (for the agent) its distinct Responses-API endpoint.

Secrets discipline is unchanged: an `api_key` lives only inside the constructed client's
`Authorization` header and is never logged or persisted here.
"""

from personal_graph_os.infrastructure.llm_chat.client import complete_json_via_chat_completions
from personal_graph_os.infrastructure.llm_chat.config import (
    ChatCompletionsProviderConfig,
    open_chat_completions_client,
)
from personal_graph_os.infrastructure.llm_chat.env_settings import (
    LlmChatSettingsError,
    ProviderSettings,
    parse_provider_settings,
)

__all__ = [
    "ChatCompletionsProviderConfig",
    "LlmChatSettingsError",
    "ProviderSettings",
    "complete_json_via_chat_completions",
    "open_chat_completions_client",
    "parse_provider_settings",
]
