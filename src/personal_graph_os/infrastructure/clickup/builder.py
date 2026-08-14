"""Fail-closed runtime composition for the configured `ClickUpClient` (EP-2026-012 ST-10),
mirroring `infrastructure.enrichment.provider_factory`.

With `PGOS_CLICKUP_API_TOKEN` unset, this returns `None` and `ClickupService` raises a typed
`ClickUpNotConfiguredError` (HTTP 503) -- no ClickUp call can ever happen and no import silently
runs unconfigured. The token is consumed only by the client instance; it is never stored in the
database, written to logs, or exposed through any response/export.

Lives under `infrastructure/`, not `application/`, because it imports the concrete
`HttpClickUpClient` (dependency direction: application depends on nothing concrete).
"""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from personal_graph_os.application.clickup_adapters import ClickUpClient
from personal_graph_os.infrastructure.clickup.clickup_client import (
    DEFAULT_CLICKUP_BASE_URL,
    HttpClickUpClient,
)

_ENV_API_TOKEN = "PGOS_CLICKUP_API_TOKEN"
_ENV_BASE_URL = "PGOS_CLICKUP_BASE_URL"


def build_clickup_client_from_env(
    environ: Mapping[str, str], *, transport: httpx.BaseTransport | None = None
) -> ClickUpClient | None:
    """Return the configured `ClickUpClient`, or `None` when ClickUp is not configured at all
    (`PGOS_CLICKUP_API_TOKEN` unset or blank -- the default, fully local posture with no ClickUp
    integration and no external calls).

    `transport` is a testability seam only; real callers never pass it.
    """
    api_token = (environ.get(_ENV_API_TOKEN) or "").strip()
    if not api_token:
        return None
    base_url = (environ.get(_ENV_BASE_URL) or "").strip() or DEFAULT_CLICKUP_BASE_URL
    return HttpClickUpClient(api_token=api_token, base_url=base_url, transport=transport)
