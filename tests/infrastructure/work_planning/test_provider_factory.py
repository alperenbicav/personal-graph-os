from __future__ import annotations

import json

import httpx
import pytest

from personal_graph_os.infrastructure.work_planning.http_chat_provider import (
    HttpChatWorkPlanningProvider,
)
from personal_graph_os.infrastructure.work_planning.provider_factory import (
    WorkPlanningProviderConfigurationError,
    build_work_planning_provider_from_env,
)

_FULL_ENV = {
    "PGOS_WORK_PLANNING_PROVIDER": "http_chat",
    "PGOS_WORK_PLANNING_BASE_URL": "https://example.test",
    "PGOS_WORK_PLANNING_API_KEY": "test-key",
    "PGOS_WORK_PLANNING_MODEL": "test-model",
}


def test_returns_none_when_planning_is_not_configured() -> None:
    assert build_work_planning_provider_from_env({}) is None
    assert build_work_planning_provider_from_env({"PGOS_WORK_PLANNING_PROVIDER": "  "}) is None


def test_raises_on_an_unrecognized_provider_name() -> None:
    with pytest.raises(WorkPlanningProviderConfigurationError):
        build_work_planning_provider_from_env({"PGOS_WORK_PLANNING_PROVIDER": "not-a-provider"})


@pytest.mark.parametrize(
    "missing_key",
    [
        "PGOS_WORK_PLANNING_BASE_URL",
        "PGOS_WORK_PLANNING_API_KEY",
        "PGOS_WORK_PLANNING_MODEL",
    ],
)
def test_raises_on_a_missing_required_setting(missing_key: str) -> None:
    incomplete_env = {key: value for key, value in _FULL_ENV.items() if key != missing_key}
    with pytest.raises(WorkPlanningProviderConfigurationError):
        build_work_planning_provider_from_env(incomplete_env)


def test_builds_the_configured_http_chat_provider() -> None:
    provider = build_work_planning_provider_from_env(_FULL_ENV)
    assert isinstance(provider, HttpChatWorkPlanningProvider)
    assert provider.name == "http-chat:test-model"


def test_composition_reaches_the_configured_adapter_end_to_end() -> None:
    """Review finding S5-R01: a captured piece of text, run through the factory-composed
    provider, must actually reach the configured HTTP adapter -- proven here via an injected
    `httpx.MockTransport`, never a live network call."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "is_justified": True,
                                    "reason": "from the configured adapter",
                                    "epic": {
                                        "title": "Epic",
                                        "work_type": "feature",
                                        "description": "",
                                    },
                                    "stories": [
                                        {
                                            "title": "Story",
                                            "work_type": "feature",
                                            "description": "",
                                            "tasks": [
                                                {
                                                    "title": "Task",
                                                    "work_type": "feature",
                                                    "description": "",
                                                }
                                            ],
                                        }
                                    ],
                                    "plan_title": "Plan",
                                    "plan_body_markdown": "body",
                                }
                            )
                        }
                    }
                ]
            },
        )

    provider = build_work_planning_provider_from_env(
        _FULL_ENV, transport=httpx.MockTransport(handler)
    )
    assert provider is not None

    result = provider.classify(source_text="Build something big.", title="A title")

    assert result.is_justified is True
    assert result.reason == "from the configured adapter"
