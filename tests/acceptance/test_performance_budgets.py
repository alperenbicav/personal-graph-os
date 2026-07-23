"""ST-09.3: the opt-in, single-client, synthetic performance acceptance gate. Never part of
the default `uv run pytest -q` run — this is not a load/stress test, and its numbers are
inherently environment-sensitive, so it stays behind the `slow` marker like the
representative-fixture build it depends on.
"""

from __future__ import annotations

import pytest

from tests.acceptance.performance_fixture import measure_backend_budgets
from tests.acceptance.running_server import RunningServer
from tests.acceptance.scale_fixture import build_representative_fixture


@pytest.mark.slow
def test_backend_budgets_on_the_representative_fixture(running_server: RunningServer) -> None:
    summary = build_representative_fixture(running_server.base_url, running_server.token)
    report = measure_backend_budgets(
        running_server.base_url, running_server.token, summary.workspace_id
    )

    failure_detail = ", ".join(
        f"{result.name}: p95={result.p95_seconds:.3f}s budget={result.budget_seconds}s "
        f"{'PASS' if result.passed else 'FAIL'}"
        for result in report.results
    )
    assert report.passed, failure_detail
