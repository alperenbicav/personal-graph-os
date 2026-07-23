from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.acceptance.running_server import RunningServer, free_loopback_port


@pytest.fixture
def running_server(tmp_path: Path) -> Iterator[RunningServer]:
    server = RunningServer(tmp_path / "acceptance-workspace.db", free_loopback_port())
    server.start()
    try:
        yield server
    finally:
        server.stop()
