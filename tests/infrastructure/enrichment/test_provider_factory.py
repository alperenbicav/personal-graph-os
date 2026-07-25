from __future__ import annotations

import json
import sqlite3

import httpx
import pytest

from personal_graph_os.application.default_schema import seed_default_schema
from personal_graph_os.application.enrichment_service import EnrichmentService
from personal_graph_os.application.semantic_schema import ensure_semantic_schema
from personal_graph_os.application.services import ResourceService, new_workspace
from personal_graph_os.domain.extraction import EvidenceKind, ExtractedContent, ExtractionEvidence
from personal_graph_os.domain.resource import ResourceKind
from personal_graph_os.infrastructure.enrichment.http_chat_provider import (
    HttpChatEnrichmentProvider,
)
from personal_graph_os.infrastructure.enrichment.provider_factory import (
    EnrichmentProviderConfigurationError,
    build_enrichment_provider_from_env,
)
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteResourceRepository,
    SqliteWorkspaceRepository,
)
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import (
    SqliteResearchUnitOfWork,
)

_FULL_ENV = {
    "PGOS_ENRICHMENT_PROVIDER": "http_chat",
    "PGOS_ENRICHMENT_BASE_URL": "https://example.test",
    "PGOS_ENRICHMENT_API_KEY": "test-key",
    "PGOS_ENRICHMENT_MODEL": "test-model",
}


def test_returns_none_when_enrichment_is_not_configured() -> None:
    assert build_enrichment_provider_from_env({}) is None
    assert build_enrichment_provider_from_env({"PGOS_ENRICHMENT_PROVIDER": "  "}) is None


def test_raises_on_an_unrecognized_provider_name() -> None:
    with pytest.raises(EnrichmentProviderConfigurationError):
        build_enrichment_provider_from_env({"PGOS_ENRICHMENT_PROVIDER": "not-a-real-provider"})


@pytest.mark.parametrize(
    "missing_key",
    ["PGOS_ENRICHMENT_BASE_URL", "PGOS_ENRICHMENT_API_KEY", "PGOS_ENRICHMENT_MODEL"],
)
def test_raises_on_a_missing_required_setting(missing_key: str) -> None:
    incomplete_env = {key: value for key, value in _FULL_ENV.items() if key != missing_key}
    with pytest.raises(EnrichmentProviderConfigurationError):
        build_enrichment_provider_from_env(incomplete_env)


def test_builds_the_configured_http_chat_provider() -> None:
    provider = build_enrichment_provider_from_env(_FULL_ENV)
    assert isinstance(provider, HttpChatEnrichmentProvider)
    assert provider.name == "http-chat:test-model"


def test_composition_reaches_the_configured_adapter_end_to_end(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """Review finding S4-R03, round 2: a real extraction, run through the factory-composed
    provider and `EnrichmentService`, must actually reach the configured HTTP adapter -- proven
    here via an injected `httpx.MockTransport`, never a live network call."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "payload": {"summary": "from the configured adapter"},
                                    "confidence": 0.8,
                                }
                            )
                        }
                    }
                ]
            },
        )

    provider = build_enrichment_provider_from_env(_FULL_ENV, transport=httpx.MockTransport(handler))
    assert provider is not None

    workspace = ensure_semantic_schema(seed_default_schema(new_workspace("Personal")))
    SqliteWorkspaceRepository(sqlite_connection).save(workspace)
    resource_service = ResourceService(
        SqliteWorkspaceRepository(sqlite_connection),
        SqliteResourceRepository(sqlite_connection),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )
    resource, _ = resource_service.create_or_reuse(
        workspace.id, "A great paper", "https://arxiv.org/abs/2401.00001"
    )
    enrichment_service = EnrichmentService(
        SqliteWorkspaceRepository(sqlite_connection),
        provider,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )
    extracted = ExtractedContent(
        resource_kind=ResourceKind.PAPER,
        canonical_identifier=resource.canonical_identifier,
        title="A Test Paper",
        abstract="An abstract.",
        evidence=(
            ExtractionEvidence(
                kind=EvidenceKind.METADATA_LOOKUP,
                adapter_name="test-adapter",
                source_reference=resource.canonical_identifier,
                content_hash="a" * 64,
                byte_length=32,
            ),
        ),
    )

    outcome = enrichment_service.enrich_resource(
        workspace_id=workspace.id,
        resource_id=resource.id,
        extracted=extracted,
        actor="agent:test",
    )

    assert outcome.version.payload.summary == "from the configured adapter"
