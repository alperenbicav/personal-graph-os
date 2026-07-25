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

_HTTP_CHAT_PROVIDER_NAME = "http_chat"
_SUPPORTED_PROVIDER_NAMES = frozenset({_HTTP_CHAT_PROVIDER_NAME})

_ENV_PROVIDER = "PGOS_ENRICHMENT_PROVIDER"
_ENV_BASE_URL = "PGOS_ENRICHMENT_BASE_URL"
_ENV_API_KEY = "PGOS_ENRICHMENT_API_KEY"
_ENV_MODEL = "PGOS_ENRICHMENT_MODEL"


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
    provider_name = (environ.get(_ENV_PROVIDER) or "").strip()
    if not provider_name:
        return None
    if provider_name not in _SUPPORTED_PROVIDER_NAMES:
        raise EnrichmentProviderConfigurationError(
            f"{_ENV_PROVIDER}={provider_name!r} is not a supported enrichment provider "
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
        raise EnrichmentProviderConfigurationError(
            f"{_ENV_PROVIDER}={_HTTP_CHAT_PROVIDER_NAME} requires {missing}, none of which may "
            "be empty"
        )

    return HttpChatEnrichmentProvider(
        ChatCompletionsProviderConfig(base_url=base_url, api_key=api_key, model_name=model_name),
        transport=transport,
    )
