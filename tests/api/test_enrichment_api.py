from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from personal_graph_os.api.app import create_app
from personal_graph_os.api.dependencies import get_extraction_service
from personal_graph_os.domain.extraction import EvidenceKind, ExtractedContent, ExtractionEvidence
from personal_graph_os.domain.resource import ResourceKind

_ENV = {
    "PGOS_ENRICHMENT_PROVIDER": "http_chat",
    "PGOS_ENRICHMENT_BASE_URL": "https://example.test",
    "PGOS_ENRICHMENT_API_KEY": "test-key",
    "PGOS_ENRICHMENT_MODEL": "test-model",
}


def _mock_chat_transport() -> httpx.MockTransport:
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

    return httpx.MockTransport(handler)


class _FakeExtractionService:
    """Stands in for `ExtractionService` (a separately tested, ST-03 concern): returns a fixed
    `ExtractedContent` for whatever resource is asked about, so this test proves the enrichment
    route/composition wiring itself without depending on real network extraction."""

    def extract(
        self, *, resource_kind: ResourceKind, canonical_identifier: str, source_url: str | None
    ) -> ExtractedContent:
        return ExtractedContent(
            resource_kind=resource_kind,
            canonical_identifier=canonical_identifier,
            title="A Test Paper",
            abstract="An abstract.",
            evidence=(
                ExtractionEvidence(
                    kind=EvidenceKind.METADATA_LOOKUP,
                    adapter_name="test-adapter",
                    source_reference=canonical_identifier,
                    content_hash="a" * 64,
                    byte_length=32,
                ),
            ),
        )


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    for key, value in _ENV.items():
        monkeypatch.setenv(key, value)
    app = create_app(
        tmp_path / "test-workspace.db", enrichment_provider_transport=_mock_chat_transport()
    )
    app.dependency_overrides[get_extraction_service] = lambda: _FakeExtractionService()
    test_client = TestClient(app)
    test_client.headers.update({"Authorization": f"Bearer {app.state.api_token}"})
    return test_client


def _workspace_id(client: TestClient) -> str:
    return client.get("/workspace").json()["id"]


def test_enrich_resource_reaches_the_configured_provider_and_persists_a_result(
    client: TestClient,
) -> None:
    """Review finding S4-R03: proves the composition root (env config -> provider factory ->
    `EnrichmentService`) is actually reachable through a real product entrypoint, not only
    through a hand-constructed test."""
    workspace_id = _workspace_id(client)
    resource = client.post(
        "/resources",
        json={
            "workspace_id": workspace_id,
            "title": "A great paper",
            "raw_source": "https://arxiv.org/abs/2401.00001",
        },
    ).json()

    response = client.post(f"/resources/{resource['id']}/enrichment", json={"actor": "agent:test"})

    assert response.status_code == 200
    body = response.json()
    assert body["version"]["payload"]["summary"] == "from the configured adapter"
    assert body["profile"]["canonical_identifier"] == resource["canonical_identifier"]


def test_enrich_resource_fails_closed_when_unconfigured(tmp_path: Path) -> None:
    app = create_app(tmp_path / "unconfigured-workspace.db")
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {app.state.api_token}"})
    workspace_id = client.get("/workspace").json()["id"]
    resource = client.post(
        "/resources",
        json={
            "workspace_id": workspace_id,
            "title": "A great paper",
            "raw_source": "https://arxiv.org/abs/2401.00001",
        },
    ).json()

    response = client.post(f"/resources/{resource['id']}/enrichment", json={"actor": "agent:test"})

    assert response.status_code == 503
