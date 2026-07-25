from __future__ import annotations

import json

import httpx
import pytest

from personal_graph_os.application.enrichment_adapters import EnrichmentProviderUnavailableError
from personal_graph_os.domain.enrichment import PaperEnrichmentPayload, ProposedRelationKind
from personal_graph_os.domain.extraction import (
    EvidenceKind,
    ExtractedContent,
    ExtractionEvidence,
    hash_content,
)
from personal_graph_os.domain.resource import ResourceKind
from personal_graph_os.infrastructure.enrichment.http_chat_provider import (
    ChatCompletionsProviderConfig,
    EnrichmentProviderResponseInvalidError,
    HttpChatEnrichmentProvider,
)

_EVIDENCE_HASH = hash_content("evidence-body")


def _extracted() -> ExtractedContent:
    return ExtractedContent(
        resource_kind=ResourceKind.PAPER,
        canonical_identifier="arxiv:2401.00001",
        title="A Test Paper",
        abstract="An abstract about testing.",
        topics=("testing",),
        evidence=(
            ExtractionEvidence(
                kind=EvidenceKind.METADATA_LOOKUP,
                adapter_name="test-adapter",
                source_reference="arxiv:2401.00001",
                content_hash=_EVIDENCE_HASH,
                byte_length=32,
            ),
        ),
    )


def _provider(handler) -> HttpChatEnrichmentProvider:
    config = ChatCompletionsProviderConfig(
        base_url="https://example.test", api_key="test-key", model_name="test-model"
    )
    return HttpChatEnrichmentProvider(config, transport=httpx.MockTransport(handler))


def _chat_response(payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": json.dumps(payload)}}]},
    )


def test_classify_parses_a_well_formed_chat_completions_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        # Prompt/data separation: the source content is only ever in the user message, inside a
        # DATA block -- never merged into the system prompt.
        assert "DATA" in body["messages"][1]["content"]
        return _chat_response(
            {
                "payload": {"summary": "a concise summary", "key_findings": ["finding one"]},
                "tags": ["testing"],
                "confidence": 0.85,
                "proposed_relations": [
                    {
                        "candidate_label": "github:example/repo",
                        "relation_kind": "relates_to",
                        "confidence": 0.9,
                        "explanation": "the paper describes this repository",
                    }
                ],
            }
        )

    provider = _provider(handler)
    result = provider.classify(extracted=_extracted())

    assert isinstance(result.payload, PaperEnrichmentPayload)
    assert result.payload.summary == "a concise summary"
    assert result.confidence == 0.85
    assert result.proposed_relations[0].relation_kind is ProposedRelationKind.RELATES_TO
    assert result.proposed_relations[0].candidate_label == "github:example/repo"


def test_classify_raises_typed_error_on_transport_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = _provider(handler)

    with pytest.raises(EnrichmentProviderUnavailableError):
        provider.classify(extracted=_extracted())


def test_classify_raises_typed_error_on_http_error_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal"})

    provider = _provider(handler)

    with pytest.raises(EnrichmentProviderUnavailableError):
        provider.classify(extracted=_extracted())


def test_classify_raises_typed_error_on_malformed_json_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    provider = _provider(handler)

    with pytest.raises(EnrichmentProviderResponseInvalidError):
        provider.classify(extracted=_extracted())


def test_classify_raises_typed_error_on_schema_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # Missing required "confidence" and a payload shape that does not validate.
        return _chat_response({"payload": {"summary": "ok"}})

    provider = _provider(handler)

    with pytest.raises(EnrichmentProviderResponseInvalidError):
        provider.classify(extracted=_extracted())


def test_classify_raises_typed_error_when_response_is_not_a_json_object() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "42"}}]})

    provider = _provider(handler)

    with pytest.raises(EnrichmentProviderResponseInvalidError):
        provider.classify(extracted=_extracted())
