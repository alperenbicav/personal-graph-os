from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from personal_graph_os.api.app import create_app
from personal_graph_os.application.resource_content_service import ResourceContentService
from personal_graph_os.domain.extraction import EvidenceKind, ExtractedContent, ExtractionEvidence
from personal_graph_os.domain.resource import ResourceKind
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteResourceContentRepository,
    SqliteResourceRepository,
)


class _FakeExtractionService:
    def __init__(self, body_markdown: str | None = "# Hello\n\nFull text.") -> None:
        self.body_markdown = body_markdown

    def extract(
        self, *, resource_kind: ResourceKind, canonical_identifier: str, source_url: str | None
    ) -> ExtractedContent:
        return ExtractedContent(
            resource_kind=resource_kind,
            canonical_identifier=canonical_identifier,
            title="A Test Paper",
            body_markdown=self.body_markdown,
            evidence=(
                ExtractionEvidence(
                    kind=EvidenceKind.FETCHED_CONTENT,
                    adapter_name="test-adapter",
                    source_reference=canonical_identifier,
                    content_hash="a" * 64,
                    byte_length=32,
                ),
            ),
        )


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = create_app(tmp_path / "test-workspace.db")
    app.state.resource_content_service = ResourceContentService(
        SqliteResourceRepository(app.state.connection),
        SqliteResourceContentRepository(app.state.connection),
        _FakeExtractionService(),  # type: ignore[arg-type]
    )
    test_client = TestClient(app)
    test_client.headers.update({"Authorization": f"Bearer {app.state.api_token}"})
    return test_client


def _create_paper(client: TestClient) -> str:
    workspace_id = client.get("/workspace").json()["id"]
    response = client.post(
        "/resources",
        json={
            "workspace_id": workspace_id,
            "title": "A paper",
            "raw_source": "https://arxiv.org/abs/2401.00001",
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_get_content_is_404_when_never_fetched(client: TestClient) -> None:
    resource_id = _create_paper(client)

    response = client.get(f"/resources/{resource_id}/content")

    assert response.status_code == 404
    assert "no persisted source content" in response.json()["detail"]


def test_refresh_then_get_returns_the_persisted_body(client: TestClient) -> None:
    resource_id = _create_paper(client)

    refreshed = client.post(f"/resources/{resource_id}/content/refresh")
    assert refreshed.status_code == 200
    body = refreshed.json()
    assert body["body_markdown"] == "# Hello\n\nFull text."
    assert body["adapter_name"] == "test-adapter"
    assert body["resource_id"] == resource_id

    loaded = client.get(f"/resources/{resource_id}/content")
    assert loaded.status_code == 200
    assert loaded.json()["body_markdown"] == body["body_markdown"]
    assert loaded.json()["content_hash"] == body["content_hash"]


def test_unknown_resource_content_routes_are_404(client: TestClient) -> None:
    assert client.get("/resources/missing/content").status_code == 404
    assert client.post("/resources/missing/content/refresh").status_code == 404


def test_refresh_with_no_readable_body_is_422(tmp_path: Path) -> None:
    app = create_app(tmp_path / "test-workspace.db")
    app.state.resource_content_service = ResourceContentService(
        SqliteResourceRepository(app.state.connection),
        SqliteResourceContentRepository(app.state.connection),
        _FakeExtractionService(body_markdown=None),  # type: ignore[arg-type]
    )
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {app.state.api_token}"})
    resource_id = _create_paper(client)

    response = client.post(f"/resources/{resource_id}/content/refresh")

    assert response.status_code == 422
    assert "no readable body" in response.json()["detail"]
