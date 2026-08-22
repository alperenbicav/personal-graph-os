"""Shared `PGOS_*` environment parsing for opt-in, fail-closed chat providers (ST-06).

Every provider factory follows the same contract: unset/blank `PGOS_<FEATURE>_PROVIDER` means
"not configured" (`None`, no external calls ever); an unrecognized value or a recognized one
missing required settings fails closed. Only the error *messages* differ per feature, so this
module returns structured data / structured failures and each factory renders its own typed
error with its own wording.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

_DEFAULT_SUPPORTED_PROVIDER_NAMES = frozenset({"http_chat"})


class LlmChatSettingsError(Exception):
    """Structured failure from `parse_provider_settings`.

    Exactly one of the field groups is populated: `unsupported` for an unrecognized provider
    name, `missing` for a recognized one with empty required settings. Factories turn these
    into their own typed configuration errors.
    """

    def __init__(
        self,
        *,
        provider_env: str,
        provider_value: str,
        supported: frozenset[str],
        unsupported: bool = False,
        missing: list[str] | None = None,
    ) -> None:
        self.provider_env = provider_env
        self.provider_value = provider_value
        self.supported = supported
        self.unsupported = unsupported
        self.missing = missing or []
        super().__init__(self._describe())

    def _describe(self) -> str:
        if self.unsupported:
            return (
                f"{self.provider_env}={self.provider_value!r} is not a supported provider "
                f"(supported: {sorted(self.supported)})"
            )
        return f"{self.provider_env}={self.provider_value!r} is missing required settings"


@dataclass(frozen=True)
class ProviderSettings:
    """Validated raw settings for one configured chat provider."""

    provider_value: str
    base_url: str
    api_key: str
    model_name: str
    reasoning_effort: str | None


def parse_provider_settings(
    environ: Mapping[str, str],
    *,
    env_prefix: str,
    supported_provider_names: frozenset[str] = _DEFAULT_SUPPORTED_PROVIDER_NAMES,
) -> ProviderSettings | None:
    """Parse `PGOS_{env_prefix}_{PROVIDER,BASE_URL,API_KEY,MODEL,REASONING_EFFORT}`.

    Returns `None` when the provider variable is unset or blank (the local-only default).
    Raises `LlmChatSettingsError` for an unrecognized provider name or a recognized one with
    blank required settings.
    """
    provider_var = f"PGOS_{env_prefix}_PROVIDER"
    base_url_var = f"PGOS_{env_prefix}_BASE_URL"
    api_key_var = f"PGOS_{env_prefix}_API_KEY"
    model_var = f"PGOS_{env_prefix}_MODEL"
    reasoning_effort_var = f"PGOS_{env_prefix}_REASONING_EFFORT"

    provider_value = (environ.get(provider_var) or "").strip()
    if not provider_value:
        return None
    if provider_value not in supported_provider_names:
        raise LlmChatSettingsError(
            provider_env=provider_var,
            provider_value=provider_value,
            supported=supported_provider_names,
            unsupported=True,
        )

    base_url = (environ.get(base_url_var) or "").strip()
    api_key = (environ.get(api_key_var) or "").strip()
    model_name = (environ.get(model_var) or "").strip()
    missing = [
        variable
        for variable, value in (
            (base_url_var, base_url),
            (api_key_var, api_key),
            (model_var, model_name),
        )
        if not value
    ]
    if missing:
        raise LlmChatSettingsError(
            provider_env=provider_var,
            provider_value=provider_value,
            supported=supported_provider_names,
            missing=missing,
        )

    return ProviderSettings(
        provider_value=provider_value,
        base_url=base_url,
        api_key=api_key,
        model_name=model_name,
        reasoning_effort=(environ.get(reasoning_effort_var) or "").strip() or None,
    )
