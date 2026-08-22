"""Fail-closed runtime composition for the configured `EnrichmentProvider` (EP-2026-012 ST-04,
review finding S4-R03).

`EnrichmentService` never chooses a provider implementation itself; this module is the one
composition point the product's REST composition root (`api/app.py`, wired to the
`POST /resources/{resource_id}/enrichment` route) calls to obtain one, mirroring the `PGOS_*`
environment-variable convention `api/__main__.py` already uses for its own configuration.
Enrichment is opt-in and local-only by default: with `PGOS_ENRICHMENT_PROVIDER` unset, this
returns `None` and no external call can ever happen. Setting it to an unrecognized value, or to a
recognized one missing a required setting, fails closed with a typed configuration error rather
than silently falling back to another provider or running unconfigured.

Lives under `infrastructure/`, not `application/`, because it imports a concrete adapter
(`HttpChatEnrichmentProvider`) -- `domain`/`application` depend on nothing concrete
(`tests/test_dependency_direction.py`); only infrastructure and the composition root may.

Live paid execution remains separate, future, separately-approved scope: this module only builds
the adapter object. Nothing here performs a network call by itself.
"""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from personal_graph_os.application.enrichment_adapters import EnrichmentProvider
from personal_graph_os.domain.enrichment import EnrichmentError
from personal_graph_os.infrastructure.enrichment.http_chat_provider import (
    ChatCompletionsProviderConfig,
    HttpChatEnrichmentProvider,
)
from personal_graph_os.infrastructure.llm_chat import (
    LlmChatSettingsError,
    parse_provider_settings,
)

_HTTP_CHAT_PROVIDER_NAME = "http_chat"
_SUPPORTED_PROVIDER_NAMES = frozenset({_HTTP_CHAT_PROVIDER_NAME})


class EnrichmentProviderConfigurationError(EnrichmentError):
    """Raised when `PGOS_ENRICHMENT_PROVIDER` names an unrecognized provider, or a recognized one
    is missing a required setting -- fails closed rather than silently running unconfigured or
    falling back to a different provider than the one requested."""


def build_enrichment_provider_from_env(
    environ: Mapping[str, str], *, transport: httpx.BaseTransport | None = None
) -> EnrichmentProvider | None:
    """Return the configured `EnrichmentProvider`, or `None` if enrichment is not configured at
    all (`PGOS_ENRICHMENT_PROVIDER` unset or blank -- the default, local-only, no external calls).

    `transport` is a testability seam only (mirrors `HttpContentFetcher`'s own `transport`
    parameter): real callers never pass it, so a real `httpx.Client` is used; tests inject an
    `httpx.MockTransport` to prove wiring end-to-end without a live network call.

    Raises `EnrichmentProviderConfigurationError` if `PGOS_ENRICHMENT_PROVIDER` names an
    unrecognized provider, or a recognized one is missing a required setting.
    """
    try:
        settings = parse_provider_settings(
            environ, env_prefix="ENRICHMENT", supported_provider_names=_SUPPORTED_PROVIDER_NAMES
        )
    except LlmChatSettingsError as error:
        if error.unsupported:
            raise EnrichmentProviderConfigurationError(
                f"{error.provider_env}={error.provider_value!r} is not a supported enrichment "
                f"provider (supported: {sorted(error.supported)})"
            ) from error
        raise EnrichmentProviderConfigurationError(
            f"{error.provider_env}={_HTTP_CHAT_PROVIDER_NAME} requires {error.missing}, none of "
            "which may be empty"
        ) from error

    if settings is None:
        return None
    return HttpChatEnrichmentProvider(
        ChatCompletionsProviderConfig(
            base_url=settings.base_url,
            api_key=settings.api_key,
            model_name=settings.model_name,
            reasoning_effort=settings.reasoning_effort,
        ),
        transport=transport,
    )
