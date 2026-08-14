"""ST-12 golden fixtures (EP-2026-012 acceptance 4): one end-to-end pass over the named capture →
search → deep-link flows, exercising the real REST surface with the same application services the
UI/MCP use.

Covers paper / repository / article / raw / planned / wiki-document creation, scoped ranked search
(score/excerpt/entity type/source), and the canonical `goto` deep-link id resolving to the created
entity. Runs against a fresh app (offline providers faked via transports), so it is deterministic
and needs no live network, model, or ClickUp/Telegram.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from personal_graph_os.api.app import create_app

_PLANNING_ENV = {
    "PGOS_WORK_PLANNING_PROVIDER": "http_chat",
    "PGOS_WORK_PLANNING_BASE_URL": "https://example.test",
    "PGOS_WORK_PLANNING_API_KEY": "test-key",
    "PGOS_WORK_PLANNING_MODEL": "test-model",
}


def _planning_transport() -> httpx.MockTransport:
    content = {
        "is_justified": True,
        "reason": "worth planning",
        "epic": {"title": "Golden Epic", "work_type": "feature", "description": ""},
        "stories": [
            {
                "title": "Golden Story",
                "work_type": "feature",
                "description": "",
                "tasks": [{"title": "Golden Task", "work_type": "feature", "description": ""}],
            }
        ],
        "plan_title": "Golden Plan",
        "plan_body_markdown": "golden plan body",
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(content)}}]}
        )

    return httpx.MockTransport(handler)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    for key, value in _PLANNING_ENV.items():
        monkeypatch.setenv(key, value)
    app = create_app(
        tmp_path / "golden-workspace.db",
        work_planning_provider_transport=_planning_transport(),
    )
    test_client = TestClient(app)
    test_client.headers.update({"Authorization": f"Bearer {app.state.api_token}"})
    return test_client


def _workspace(client: TestClient) -> dict:
    return client.get("/workspace").json()


def test_golden_capture_to_scoped_search_to_deep_link_flows(client: TestClient) -> None:
    workspace = _workspace(client)
    workspace_id = workspace["id"]

    paper = client.post(
        "/capture",
        json={
            "workspace_id": workspace_id,
            "source": "golden",
            "request_id": "paper-1",
            "actor_name": "golden",
            "payload_kind": "url",
            "url": "https://arxiv.org/abs/2401.00001",
            "intent": "save_raw",
        },
    ).json()["capture"]
    assert paper["resource_id"] is not None

    repository = client.post(
        "/capture",
        json={
            "workspace_id": workspace_id,
            "source": "golden",
            "request_id": "repo-1",
            "actor_name": "golden",
            "payload_kind": "url",
            "url": "https://github.com/apilex-ai/example",
            "intent": "save_raw",
        },
    ).json()["capture"]
    assert repository["resource_id"] is not None

    article = client.post(
        "/capture",
        json={
            "workspace_id": workspace_id,
            "source": "golden",
            "request_id": "article-1",
            "actor_name": "golden",
            "payload_kind": "url",
            "url": "https://example.com/golden-article",
            "intent": "save_raw",
        },
    ).json()["capture"]
    assert article["resource_id"] is not None

    raw = client.post(
        "/capture",
        json={
            "workspace_id": workspace_id,
            "source": "golden",
            "request_id": "raw-1",
            "actor_name": "golden",
            "payload_kind": "text",
            "text": "a golden raw inbox note",
            "intent": "save_raw",
        },
    ).json()["capture"]
    assert raw["document_id"] is not None

    planned = client.post(
        "/capture",
        json={
            "workspace_id": workspace_id,
            "source": "golden",
            "request_id": "plan-1",
            "actor_name": "golden",
            "payload_kind": "text",
            "text": "plan this",
            "intent": "plan_work",
        },
    ).json()
    assert planned["plan"] is not None
    assert planned["plan"]["epic"]["id"] is not None

    document = client.post(
        "/documents",
        json={
            "workspace_id": workspace_id,
            "title": "golden wiki document",
            "kind": "note",
            "source": "manual",
            "body_markdown": "a golden wiki body",
        },
    ).json()
    assert document["id"] is not None

    # -- scoped search: every scope returns only its tab's objects --
    # Each (scope, query, expected) uses a token that exists only in that tab's indexed text.
    scopes = [
        ("research", "arxiv", "arxiv.org"),
        ("research", "golden-article", "golden-article"),
        ("repositories", "github.com", "github.com"),
        ("tasks", "Golden Epic", "Golden Epic"),
        ("wiki", "golden wiki", "golden wiki"),
    ]
    for scope, query, expected in scopes:
        page = client.get(
            "/search", params={"workspace_id": workspace_id, "q": query, "scope": scope}
        ).json()
        titles = " ".join(
            r["node"]["title"] if r["node"] else (r["document"]["title"] if r["document"] else "")
            for r in page["results"]
        )
        assert expected in titles, f"scope {scope} missing {expected!r} (q={query!r}): {titles}"

    # A `graph` search never leaks a work-item/resource/wiki result into the generic scope.
    graph_page = client.get(
        "/search", params={"workspace_id": workspace_id, "q": "golden", "scope": "graph"}
    ).json()
    assert graph_page["results"] == []

    all_page = client.get(
        "/search", params={"workspace_id": workspace_id, "q": "golden", "scope": "all"}
    ).json()
    assert all_page["total"] >= 5

    # -- result projection + deep-link `goto` resolves to the created entities --
    wiki_page = client.get(
        "/search", params={"workspace_id": workspace_id, "q": "golden wiki", "scope": "wiki"}
    ).json()
    assert len(wiki_page["results"]) == 1
    hit = wiki_page["results"][0]
    assert hit["document"]["id"] == document["id"]
    assert hit["goto"] == document["id"]
    assert isinstance(hit["score"], float)
    assert hit["entity_type"] == "document"

    research_page = client.get(
        "/search", params={"workspace_id": workspace_id, "q": "arxiv", "scope": "research"}
    ).json()
    assert research_page["results"][0]["goto"] == research_page["results"][0]["node"]["id"]

    # -- pagination surfaces a correct next-page signal (S12-F01) --
    task_page = client.get(
        "/search",
        params={
            "workspace_id": workspace_id,
            "q": "golden",
            "scope": "all",
            "limit": 3,
            "offset": 0,
        },
    ).json()
    assert len(task_page["results"]) == 3
    assert task_page["has_more"] is True
    assert task_page["total"] == all_page["total"]
