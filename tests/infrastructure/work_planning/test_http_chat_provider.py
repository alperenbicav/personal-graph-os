from __future__ import annotations

import json

import httpx
import pytest

from personal_graph_os.application.work_planning_adapters import (
    WorkPlanningProviderUnavailableError,
)
from personal_graph_os.domain.work_items import WorkItemType
from personal_graph_os.infrastructure.work_planning.http_chat_provider import (
    ChatCompletionsProviderConfig,
    HttpChatWorkPlanningProvider,
    WorkPlanningProviderResponseInvalidError,
)


def _provider(handler) -> HttpChatWorkPlanningProvider:
    config = ChatCompletionsProviderConfig(
        base_url="https://example.test", api_key="test-key", model_name="test-model"
    )
    return HttpChatWorkPlanningProvider(config, transport=httpx.MockTransport(handler))


def _chat_response(payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": json.dumps(payload)}}]},
    )


def test_classify_parses_a_justified_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        # Prompt/data separation: source text only ever appears in the user message's DATA
        # block, never merged into the system prompt.
        assert "DATA" in body["messages"][1]["content"]
        return _chat_response(
            {
                "is_justified": True,
                "reason": "multi-step effort",
                "epic": {"title": "Ship it", "work_type": "feature", "description": "desc"},
                "stories": [
                    {
                        "title": "Story 1",
                        "work_type": "feature",
                        "description": "",
                        "tasks": [{"title": "Task 1", "work_type": "feature", "description": ""}],
                    }
                ],
                "plan_title": "Plan: Ship it",
                "plan_body_markdown": "# Ship it\n\ndetails",
            }
        )

    provider = _provider(handler)
    result = provider.classify(source_text="Build a large feature.", title="Ship it")

    assert result.is_justified is True
    assert result.epic is not None
    assert result.epic.title == "Ship it"
    assert result.epic.work_type is WorkItemType.FEATURE
    assert len(result.stories) == 1
    assert len(result.stories[0].tasks) == 1
    assert result.plan_title == "Plan: Ship it"


def test_classify_parses_a_not_justified_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _chat_response({"is_justified": False, "reason": "too small"})

    provider = _provider(handler)
    result = provider.classify(source_text="A quick note.", title="Note")

    assert result.is_justified is False
    assert result.epic is None


def test_classify_raises_typed_error_on_transport_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = _provider(handler)

    with pytest.raises(WorkPlanningProviderUnavailableError):
        provider.classify(source_text="x", title="y")


def test_classify_raises_typed_error_on_http_error_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal"})

    provider = _provider(handler)

    with pytest.raises(WorkPlanningProviderUnavailableError):
        provider.classify(source_text="x", title="y")


def test_classify_raises_typed_error_on_malformed_json_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    provider = _provider(handler)

    with pytest.raises(WorkPlanningProviderResponseInvalidError):
        provider.classify(source_text="x", title="y")


def test_classify_raises_typed_error_on_schema_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _chat_response({"is_justified": True, "reason": "ok"})

    provider = _provider(handler)

    with pytest.raises(WorkPlanningProviderResponseInvalidError):
        provider.classify(source_text="x", title="y")


def test_classify_raises_typed_error_when_response_is_not_a_json_object() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "42"}}]})

    provider = _provider(handler)

    with pytest.raises(WorkPlanningProviderResponseInvalidError):
        provider.classify(source_text="x", title="y")


def test_classify_raises_typed_error_on_an_invalid_work_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _chat_response(
            {
                "is_justified": True,
                "reason": "ok",
                "epic": {"title": "Epic", "work_type": "not-a-real-type", "description": ""},
                "stories": [
                    {
                        "title": "Story",
                        "work_type": "feature",
                        "description": "",
                        "tasks": [{"title": "Task", "work_type": "feature", "description": ""}],
                    }
                ],
                "plan_title": "Plan",
                "plan_body_markdown": "body",
            }
        )

    provider = _provider(handler)

    with pytest.raises(WorkPlanningProviderResponseInvalidError):
        provider.classify(source_text="x", title="y")
