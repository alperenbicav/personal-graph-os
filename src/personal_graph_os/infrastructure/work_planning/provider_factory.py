"""Fail-closed runtime composition for the configured `WorkPlanningProvider` (EP-2026-012 ST-05,
review finding S5-R01), mirroring `infrastructure.enrichment.provider_factory` exactly.

`WorkPlanningService` never chooses a provider implementation itself; this module is the one
composition point the product's REST composition root (`api/app.py`) calls to obtain one, using
its own `PGOS_WORK_PLANNING_*` environment-variable namespace (distinct from
`PGOS_ENRICHMENT_*`: enrichment and planning are independently configurable and independently
opt-in). With `PGOS_WORK_PLANNING_PROVIDER` unset, this returns `None` and no external call can
ever happen. Setting it to an unrecognized value, or to a recognized one missing a required
setting, fails closed with a typed configuration error rather than silently falling back to
another provider or running unconfigured.

Lives under `infrastructure/`, not `application/`, because it imports a concrete adapter
(`HttpChatWorkPlanningProvider`) -- `domain`/`application` depend on nothing concrete
(`tests/test_dependency_direction.py`); only infrastructure and the composition root may.
"""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from personal_graph_os.application.work_planning_adapters import WorkPlanningProvider
from personal_graph_os.domain.work_planning import WorkPlanningError
from personal_graph_os.infrastructure.work_planning.http_chat_provider import (
    ChatCompletionsProviderConfig,
    HttpChatWorkPlanningProvider,
)

_HTTP_CHAT_PROVIDER_NAME = "http_chat"
_SUPPORTED_PROVIDER_NAMES = frozenset({_HTTP_CHAT_PROVIDER_NAME})

_ENV_PROVIDER = "PGOS_WORK_PLANNING_PROVIDER"
_ENV_BASE_URL = "PGOS_WORK_PLANNING_BASE_URL"
_ENV_API_KEY = "PGOS_WORK_PLANNING_API_KEY"
_ENV_MODEL = "PGOS_WORK_PLANNING_MODEL"
_ENV_REASONING_EFFORT = "PGOS_WORK_PLANNING_REASONING_EFFORT"


class WorkPlanningProviderConfigurationError(WorkPlanningError):
    """Raised when `PGOS_WORK_PLANNING_PROVIDER` names an unrecognized provider, or a recognized
    one is missing a required setting -- fails closed rather than silently running unconfigured
    or falling back to a different provider than the one requested."""


def build_work_planning_provider_from_env(
    environ: Mapping[str, str], *, transport: httpx.BaseTransport | None = None
) -> WorkPlanningProvider | None:
    """Return the configured `WorkPlanningProvider`, or `None` if planning is not configured at
    all (`PGOS_WORK_PLANNING_PROVIDER` unset or blank -- the default, local-only, no external
    calls).

    `transport` is a testability seam only: real callers never pass it, so a real `httpx.Client`
    is used; tests inject an `httpx.MockTransport` to prove wiring end-to-end without a live
    network call.

    Raises `WorkPlanningProviderConfigurationError` if `PGOS_WORK_PLANNING_PROVIDER` names an
    unrecognized provider, or a recognized one is missing a required setting.
    """
    provider_name = (environ.get(_ENV_PROVIDER) or "").strip()
    if not provider_name:
        return None
    if provider_name not in _SUPPORTED_PROVIDER_NAMES:
        raise WorkPlanningProviderConfigurationError(
            f"{_ENV_PROVIDER}={provider_name!r} is not a supported work-planning provider "
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
        raise WorkPlanningProviderConfigurationError(
            f"{_ENV_PROVIDER}={_HTTP_CHAT_PROVIDER_NAME} requires {missing}, none of which may "
            "be empty"
        )

    return HttpChatWorkPlanningProvider(
        ChatCompletionsProviderConfig(
            base_url=base_url,
            api_key=api_key,
            model_name=model_name,
            reasoning_effort=(environ.get(_ENV_REASONING_EFFORT) or "").strip() or None,
        ),
        transport=transport,
    )
