"""Unit tests for the agent chat provider and its env factory (MockTransport only)."""

from __future__ import annotations

import json

import httpx

from personal_graph_os.application.agent_adapters import AgentChatProviderError, AgentTool
from personal_graph_os.infrastructure.agent.chat_provider import (
    AgentChatProvider,
    AgentChatProviderConfig,
)
from personal_graph_os.infrastructure.agent.provider_factory import (
    build_agent_chat_provider_from_env,
)

_SEARCH_TOOL = AgentTool(
    name="search_graph",
    description="Search the graph",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
)


def _provider(body: dict[str, object], *, status: int = 200) -> AgentChatProvider:
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json=body))
    return AgentChatProvider(
        AgentChatProviderConfig(
            base_url="https://example.test/v1",
            api_key="secret",
            model_name="gpt-5.6-luna",
            reasoning_effort="high",
        ),
        transport=transport,
    )


def _message_response(text: str) -> dict[str, object]:
    return {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text}],
            }
        ]
    }


def test_complete_returns_final_text() -> None:
    provider = _provider(_message_response("There are 3 papers stored."))
    turn = provider.complete(input_items=[{"role": "user", "content": "what is stored?"}])
    assert turn.final_text == "There are 3 papers stored."
    assert turn.tool_calls == ()


def test_complete_parses_tool_calls_and_sends_responses_shape() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "function_call",
                        "id": "fc_1",
                        "call_id": "call_1",
                        "name": "search_graph",
                        "arguments": '{"query": "repo"}',
                    }
                ]
            },
        )

    provider = AgentChatProvider(
        AgentChatProviderConfig(
            base_url="https://example.test/v1",
            api_key="secret",
            model_name="gpt-5.6-luna",
            reasoning_effort="high",
        ),
        transport=httpx.MockTransport(handler),
    )
    turn = provider.complete(
        input_items=[{"role": "user", "content": "find repos"}], tools=(_SEARCH_TOOL,)
    )

    assert turn.final_text is None
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].name == "search_graph"
    assert turn.tool_calls[0].arguments == {"query": "repo"}
    assert turn.output_items[0]["type"] == "function_call"

    body = captured["body"]
    assert body["reasoning"] == {"effort": "high"}
    assert body["input"][0]["role"] == "user"
    assert body["tools"][0] == {
        "type": "function",
        "name": "search_graph",
        "description": "Search the graph",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }


def test_complete_omits_tools_and_reasoning_when_unconfigured() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_message_response("hi"))

    provider = AgentChatProvider(
        AgentChatProviderConfig(
            base_url="https://example.test/v1", api_key="secret", model_name="m"
        ),
        transport=httpx.MockTransport(handler),
    )
    provider.complete(input_items=[{"role": "user", "content": "hi"}])
    assert "tools" not in captured["body"]
    assert "reasoning" not in captured["body"]


def test_complete_surfaces_transport_failure_as_provider_error() -> None:
    provider = AgentChatProvider(
        AgentChatProviderConfig(
            base_url="https://example.test/v1", api_key="secret", model_name="m"
        ),
        transport=httpx.MockTransport(lambda request: httpx.Response(500, json={})),
    )
    try:
        provider.complete(input_items=[{"role": "user", "content": "hi"}])
    except AgentChatProviderError:
        pass
    else:  # pragma: no cover - failure expected
        raise AssertionError("expected AgentChatProviderError")


def test_factory_fails_closed_when_provider_unset() -> None:
    assert build_agent_chat_provider_from_env({}) is None


def test_factory_builds_http_chat_provider_from_env() -> None:
    provider = build_agent_chat_provider_from_env(
        {
            "PGOS_AGENT_CHAT_PROVIDER": "http_chat",
            "PGOS_AGENT_CHAT_BASE_URL": "https://example.test/v1",
            "PGOS_AGENT_CHAT_API_KEY": "k",
            "PGOS_AGENT_CHAT_MODEL": "gpt-5.6-luna",
            "PGOS_AGENT_CHAT_REASONING_EFFORT": "high",
        }
    )
    assert provider is not None
    assert provider.name == "agent-chat:gpt-5.6-luna"


def test_factory_rejects_unknown_provider_and_missing_settings() -> None:
    try:
        build_agent_chat_provider_from_env({"PGOS_AGENT_CHAT_PROVIDER": "nope"})
    except AgentChatProviderError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected AgentChatProviderError")

    try:
        build_agent_chat_provider_from_env(
            {"PGOS_AGENT_CHAT_PROVIDER": "http_chat", "PGOS_AGENT_CHAT_BASE_URL": "x"}
        )
    except AgentChatProviderError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected AgentChatProviderError for missing settings")
