from __future__ import annotations

import json

import httpx
import pytest

from personal_graph_os.application.llm_provider import LlmProviderError
from personal_graph_os.infrastructure.llm.anthropic_provider import AnthropicLlmProvider
from personal_graph_os.infrastructure.llm.openai_provider import OpenAiLlmProvider
from personal_graph_os.infrastructure.llm.provider_factory import build_llm_provider_from_env


def test_build_llm_provider_returns_none_when_unconfigured() -> None:
    provider = build_llm_provider_from_env({})
    assert provider is None

    provider = build_llm_provider_from_env({"PGOS_LLM_API_KEY": ""})
    assert provider is None


def test_build_llm_provider_supports_openai_and_openrouter_and_anthropic() -> None:
    # OpenAI default
    p_openai = build_llm_provider_from_env({"PGOS_LLM_API_KEY": "test-key"})
    assert isinstance(p_openai, OpenAiLlmProvider)
    assert p_openai._base_url == "https://api.openai.com/v1"
    assert p_openai._default_model == "gpt-4o-mini"

    # OpenRouter
    p_openrouter = build_llm_provider_from_env({
        "PGOS_LLM_PROVIDER": "openrouter",
        "PGOS_LLM_API_KEY": "test-key",
    })
    assert isinstance(p_openrouter, OpenAiLlmProvider)
    assert p_openrouter._base_url == "https://openrouter.ai/api/v1"
    assert p_openrouter._default_model == "openai/gpt-4o-mini"

    # Anthropic
    p_anthropic = build_llm_provider_from_env({
        "PGOS_LLM_PROVIDER": "anthropic",
        "PGOS_LLM_API_KEY": "test-key",
    })
    assert isinstance(p_anthropic, AnthropicLlmProvider)
    assert p_anthropic._base_url == "https://api.anthropic.com/v1"
    assert p_anthropic._default_model == "claude-3-5-sonnet-20241022"

    # Base URL & Model overrides
    p_custom = build_llm_provider_from_env({
        "PGOS_LLM_PROVIDER": "openai",
        "PGOS_LLM_API_KEY": "test-key",
        "PGOS_LLM_BASE_URL": "https://custom-llm.example.com/v1",
        "PGOS_LLM_MODEL": "llama-3.3-70b",
    })
    assert isinstance(p_custom, OpenAiLlmProvider)
    assert p_custom._base_url == "https://custom-llm.example.com/v1"
    assert p_custom._default_model == "llama-3.3-70b"


def test_build_llm_provider_rejects_unsupported_provider() -> None:
    with pytest.raises(LlmProviderError, match="Unsupported PGOS_LLM_PROVIDER"):
        build_llm_provider_from_env({
            "PGOS_LLM_PROVIDER": "unknown_provider",
            "PGOS_LLM_API_KEY": "test-key",
        })


@pytest.mark.anyio
async def test_openai_provider_complete_and_stream() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        payload = json.loads(request.content.decode())
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][1]["role"] == "user"
        assert payload["model"] == "gpt-4o"

        if payload.get("stream"):
            sse_content = (
                b'data: {"choices": [{"delta": {"content": "Hello"}}]}\n\n'
                b'data: {"choices": [{"delta": {"content": " world!"}}]}\n\n'
                b'data: [DONE]\n\n'
            )
            return httpx.Response(200, content=sse_content)
        else:
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "Hello complete!"}}]
            })

    transport = httpx.MockTransport(handler)
    provider = OpenAiLlmProvider(
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        default_model="gpt-4o-mini",
        transport=transport,
    )

    # Complete with model override
    reply = await provider.complete(
        system_prompt="You are an agent",
        user_message="Hi",
        model="gpt-4o",
    )
    assert reply == "Hello complete!"

    # Stream with model override
    chunks = []
    async for chunk in provider.stream(
        system_prompt="You are an agent",
        user_message="Hi",
        model="gpt-4o",
    ):
        chunks.append(chunk)
    assert "".join(chunks) == "Hello world!"


@pytest.mark.anyio
async def test_anthropic_provider_complete_and_stream() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "anthropic-key"
        assert request.headers["anthropic-version"] == "2023-06-01"
        payload = json.loads(request.content.decode())
        assert payload["system"] == "System instructions"
        assert payload["messages"][0]["content"] == "User question"
        assert payload["model"] == "claude-3-opus"

        if payload.get("stream"):
            delta1 = json.dumps({
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "Claude"},
            }).encode()
            delta2 = json.dumps({
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": " response"},
            }).encode()
            sse_content = b"data: " + delta1 + b"\n\ndata: " + delta2 + b"\n\ndata: [DONE]\n\n"
            return httpx.Response(200, content=sse_content)
        else:
            return httpx.Response(200, json={
                "content": [{"type": "text", "text": "Claude non-streaming response"}]
            })

    transport = httpx.MockTransport(handler)
    provider = AnthropicLlmProvider(
        base_url="https://api.anthropic.com/v1",
        api_key="anthropic-key",
        default_model="claude-3-5-sonnet-20241022",
        transport=transport,
    )

    reply = await provider.complete(
        system_prompt="System instructions",
        user_message="User question",
        model="claude-3-opus",
    )
    assert reply == "Claude non-streaming response"

    chunks = []
    async for chunk in provider.stream(
        system_prompt="System instructions",
        user_message="User question",
        model="claude-3-opus",
    ):
        chunks.append(chunk)
    assert "".join(chunks) == "Claude response"
