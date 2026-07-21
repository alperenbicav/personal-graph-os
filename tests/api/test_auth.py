from __future__ import annotations

from pathlib import Path

from personal_graph_os.api.auth import get_or_create_api_token


def test_get_or_create_api_token_generates_and_persists(tmp_path: Path) -> None:
    token_path = tmp_path / "api-token"
    assert not token_path.exists()

    token = get_or_create_api_token(token_path)

    assert token_path.exists()
    assert token_path.read_text(encoding="utf-8") == token
    assert len(token) > 20


def test_get_or_create_api_token_reuses_existing_file(tmp_path: Path) -> None:
    token_path = tmp_path / "api-token"
    first = get_or_create_api_token(token_path)
    second = get_or_create_api_token(token_path)
    assert first == second


def test_get_or_create_api_token_regenerates_if_file_is_empty(tmp_path: Path) -> None:
    token_path = tmp_path / "api-token"
    token_path.write_text("", encoding="utf-8")
    token = get_or_create_api_token(token_path)
    assert token
