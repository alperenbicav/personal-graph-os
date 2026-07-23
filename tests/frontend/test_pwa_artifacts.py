"""Verifies ST-08.2's generated PWA artifacts once `npm run build` has produced them.

Not part of the default backend gate (the rest of the suite must not require a frontend
build): skipped when `frontend/dist` is absent, exactly like the Playwright acceptance this
complements. Run `npm run build` under `frontend/` first to exercise these.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

pytestmark = pytest.mark.skipif(
    not _FRONTEND_DIST.is_dir(),
    reason="requires a frontend production build (`npm run build` under frontend/) first",
)


def test_service_worker_never_routes_api_mcp_export_or_attachment_traffic() -> None:
    service_worker_source = (_FRONTEND_DIST / "sw.js").read_text(encoding="utf-8")
    for forbidden in ("/mcp", "/export", "/attachments", "runtimeCaching"):
        assert forbidden not in service_worker_source


def test_index_html_resolves_build_marker_to_canonical_default_with_no_stale_placeholder() -> None:
    index_html = (_FRONTEND_DIST / "index.html").read_text(encoding="utf-8")
    assert "%VITE_" not in index_html
    assert 'content="default"' in index_html


def test_manifest_declares_standalone_app_shell_and_required_icon_sizes() -> None:
    manifest = json.loads((_FRONTEND_DIST / "manifest.webmanifest").read_text(encoding="utf-8"))

    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "/app/"
    assert manifest["scope"] == "/app/"

    icon_sizes = {icon["sizes"] for icon in manifest["icons"]}
    assert {"192x192", "512x512"}.issubset(icon_sizes)
    assert any(icon.get("purpose") == "maskable" for icon in manifest["icons"])
