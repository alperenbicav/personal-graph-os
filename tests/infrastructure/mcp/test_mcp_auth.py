"""Unauthorized/wrong-token protocol tests must fail before any tool code runs, and REST
auth behavior must remain unchanged now that `/mcp` is mounted alongside it."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from personal_graph_os.api.app import create_app

_INITIALIZE_PAYLOAD = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test-client", "version": "0.0.1"},
    },
}

_MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def test_mcp_request_without_authorization_header_is_rejected(tmp_path: Path) -> None:
    app = create_app(tmp_path / "test-workspace.db")
    with TestClient(app) as client:
        response = client.post("/mcp", json=_INITIALIZE_PAYLOAD, headers=_MCP_HEADERS)
    assert response.status_code == 401


def test_mcp_request_with_wrong_token_is_rejected(tmp_path: Path) -> None:
    app = create_app(tmp_path / "test-workspace.db")
    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            json=_INITIALIZE_PAYLOAD,
            headers={**_MCP_HEADERS, "Authorization": "Bearer wrong-token"},
        )
    assert response.status_code == 401


def test_mcp_request_with_malformed_authorization_header_is_rejected(tmp_path: Path) -> None:
    app = create_app(tmp_path / "test-workspace.db")
    with TestClient(app) as client:
        token = app.state.api_token
        response = client.post(
            "/mcp", json=_INITIALIZE_PAYLOAD, headers={**_MCP_HEADERS, "Authorization": token}
        )
    assert response.status_code == 401


def test_mcp_request_with_correct_token_is_not_rejected_by_auth(tmp_path: Path) -> None:
    app = create_app(tmp_path / "test-workspace.db")
    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            json=_INITIALIZE_PAYLOAD,
            headers={**_MCP_HEADERS, "Authorization": f"Bearer {app.state.api_token}"},
        )
    assert response.status_code != 401


def test_mcp_mount_does_not_redirect_on_bare_path(tmp_path: Path) -> None:
    """`Mount` requires a trailing slash to match and would 307 before auth ran; the adapter
    must register `/mcp` (no trailing slash) directly instead."""
    app = create_app(tmp_path / "test-workspace.db")
    with TestClient(app, follow_redirects=False) as client:
        response = client.post("/mcp", json=_INITIALIZE_PAYLOAD, headers=_MCP_HEADERS)
    assert response.status_code != 307


def test_rest_auth_behavior_is_unchanged_by_mcp_mount(tmp_path: Path) -> None:
    app = create_app(tmp_path / "test-workspace.db")
    with TestClient(app) as client:
        unauthenticated = client.get("/workspace")
        authenticated = client.get(
            "/workspace", headers={"Authorization": f"Bearer {app.state.api_token}"}
        )
    assert unauthenticated.status_code == 401
    assert authenticated.status_code == 200
