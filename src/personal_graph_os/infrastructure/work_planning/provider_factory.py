"""Fail-closed runtime composition for the configured `WorkPlanningProvider` (EP-2026-012 ST-05,
review finding S5-R01), mirroring `infrastructure.enrichment.provider_factory`.

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
from personal_graph_os.infrastructure.llm_chat import (
    LlmChatSettingsError,
    parse_provider_settings,
)
from personal_graph_os.infrastructure.work_planning.http_chat_provider import (
    ChatCompletionsProviderConfig,
    HttpChatWorkPlanningProvider,
)

_HTTP_CHAT_PROVIDER_NAME = "http_chat"
_SUPPORTED_PROVIDER_NAMES = frozenset({_HTTP_CHAT_PROVIDER_NAME})


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
    try:
        settings = parse_provider_settings(
            environ,
            env_prefix="WORK_PLANNING",
            supported_provider_names=_SUPPORTED_PROVIDER_NAMES,
        )
    except LlmChatSettingsError as error:
        if error.unsupported:
            raise WorkPlanningProviderConfigurationError(
                f"{error.provider_env}={error.provider_value!r} is not a supported work-planning "
                f"provider (supported: {sorted(error.supported)})"
            ) from error
        raise WorkPlanningProviderConfigurationError(
            f"{error.provider_env}={_HTTP_CHAT_PROVIDER_NAME} requires {error.missing}, none of "
            "which may be empty"
        ) from error

    if settings is None:
        return None
    return HttpChatWorkPlanningProvider(
        ChatCompletionsProviderConfig(
            base_url=settings.base_url,
            api_key=settings.api_key,
            model_name=settings.model_name,
            reasoning_effort=settings.reasoning_effort,
        ),
        transport=transport,
    )
