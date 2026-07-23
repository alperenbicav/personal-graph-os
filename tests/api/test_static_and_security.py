from __future__ import annotations

import stat
from pathlib import Path

from fastapi.testclient import TestClient

from personal_graph_os.api.app import create_app


def test_no_trusted_hosts_by_default_preserves_existing_behavior(tmp_path: Path) -> None:
    app = create_app(tmp_path / "test-workspace.db")
    response = TestClient(app).get(
        "/workspace",
        headers={"Authorization": f"Bearer {app.state.api_token}", "Host": "anything-at-all"},
    )
    assert response.status_code == 200


def test_trusted_hosts_rejects_unlisted_host(tmp_path: Path) -> None:
    app = create_app(tmp_path / "test-workspace.db", trusted_hosts=["127.0.0.1", "localhost"])
    client = TestClient(app, base_url="http://untrusted-host")
    response = client.get("/workspace", headers={"Authorization": f"Bearer {app.state.api_token}"})
    assert response.status_code == 400


def test_trusted_hosts_allows_listed_host(tmp_path: Path) -> None:
    app = create_app(tmp_path / "test-workspace.db", trusted_hosts=["127.0.0.1", "localhost"])
    client = TestClient(app, base_url="http://127.0.0.1")
    response = client.get("/workspace", headers={"Authorization": f"Bearer {app.state.api_token}"})
    assert response.status_code == 200


def test_no_cors_headers_by_default(tmp_path: Path) -> None:
    app = create_app(tmp_path / "test-workspace.db")
    response = TestClient(app).get(
        "/workspace",
        headers={
            "Authorization": f"Bearer {app.state.api_token}",
            "Origin": "http://localhost:5173",
        },
    )
    assert "access-control-allow-origin" not in response.headers


def test_cors_headers_present_when_explicitly_configured(tmp_path: Path) -> None:
    app = create_app(tmp_path / "test-workspace.db", cors_origins=["http://localhost:5173"])
    response = TestClient(app).get(
        "/workspace",
        headers={
            "Authorization": f"Bearer {app.state.api_token}",
            "Origin": "http://localhost:5173",
        },
    )
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_api_responses_are_never_cached(tmp_path: Path) -> None:
    app = create_app(tmp_path / "test-workspace.db")
    response = TestClient(app).get(
        "/workspace", headers={"Authorization": f"Bearer {app.state.api_token}"}
    )
    assert response.headers["cache-control"] == "no-store"


def test_security_headers_present_on_every_response(tmp_path: Path) -> None:
    app = create_app(tmp_path / "test-workspace.db")
    response = TestClient(app).get(
        "/workspace", headers={"Authorization": f"Bearer {app.state.api_token}"}
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_no_static_mount_when_static_dir_omitted(tmp_path: Path) -> None:
    app = create_app(tmp_path / "test-workspace.db")
    response = TestClient(app).get("/app/")
    assert response.status_code == 404


def _write_fake_build(build_dir: Path) -> None:
    build_dir.mkdir(parents=True)
    (build_dir / "index.html").write_text("<html>shell</html>", encoding="utf-8")
    assets_dir = build_dir / "assets"
    assets_dir.mkdir()
    (assets_dir / "app-abc123.js").write_text("console.log('hi')", encoding="utf-8")


def test_static_dir_serves_shell_without_requiring_bearer_token(tmp_path: Path) -> None:
    build_dir = tmp_path / "dist"
    _write_fake_build(build_dir)
    app = create_app(tmp_path / "test-workspace.db", static_dir=build_dir)

    response = TestClient(app).get("/app/")

    assert response.status_code == 200
    assert "shell" in response.text
    assert response.headers["cache-control"] == "no-cache"


def test_static_hashed_assets_are_long_lived_cacheable(tmp_path: Path) -> None:
    build_dir = tmp_path / "dist"
    _write_fake_build(build_dir)
    app = create_app(tmp_path / "test-workspace.db", static_dir=build_dir)

    response = TestClient(app).get("/app/assets/app-abc123.js")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_static_mount_does_not_shadow_api_or_mcp_routes(tmp_path: Path) -> None:
    build_dir = tmp_path / "dist"
    _write_fake_build(build_dir)
    app = create_app(tmp_path / "test-workspace.db", static_dir=build_dir)
    client = TestClient(app)

    workspace_response = client.get(
        "/workspace", headers={"Authorization": f"Bearer {app.state.api_token}"}
    )
    mcp_response = client.post("/mcp")

    assert workspace_response.status_code == 200
    assert mcp_response.status_code == 401  # bearer-gated, not swallowed by the static mount


def test_credentials_cli_show_and_rotate(tmp_path: Path) -> None:
    from personal_graph_os.api.credentials_cli import main as credentials_main

    db_path = tmp_path / "test-workspace.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    app = create_app(db_path)
    original_token = app.state.api_token

    credentials_main(["--db-path", str(db_path), "show"])
    credentials_main(["--db-path", str(db_path), "rotate"])

    token_path = db_path.parent / "api-token"
    rotated_token = token_path.read_text(encoding="utf-8").strip()
    assert rotated_token != original_token
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
