"""Unit tests for the minimal `.env` loader used by `personal_graph_os.api.__main__`."""

import os
from pathlib import Path

from personal_graph_os.api.__main__ import load_dotenv


def test_loads_key_value_pairs(tmp_path: Path, monkeypatch) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "PGOS_PORT=9000\n"
        "# a comment\n"
        "\n"
        'PGOS_ENRICHMENT_MODEL="gpt-5.6-luna"\n'
        "PGOS_TRUSTED_HOSTS=127.0.0.1, localhost\n"
    )
    monkeypatch.delenv("PGOS_PORT", raising=False)
    monkeypatch.delenv("PGOS_ENRICHMENT_MODEL", raising=False)
    monkeypatch.delenv("PGOS_TRUSTED_HOSTS", raising=False)
    load_dotenv(dotenv)
    assert os.environ["PGOS_PORT"] == "9000"
    assert os.environ["PGOS_ENRICHMENT_MODEL"] == "gpt-5.6-luna"
    assert os.environ["PGOS_TRUSTED_HOSTS"] == "127.0.0.1, localhost"


def test_never_overrides_existing_environment(tmp_path: Path, monkeypatch) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("PGOS_PORT=9000\n")
    monkeypatch.setenv("PGOS_PORT", "7000")
    load_dotenv(dotenv)
    assert os.environ["PGOS_PORT"] == "7000"


def test_missing_file_is_a_no_op(tmp_path: Path) -> None:
    load_dotenv(tmp_path / "does-not-exist.env")
    assert "PGOS_ENRICHMENT_API_KEY" not in os.environ
