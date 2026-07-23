"""ST-09.3: measures REST-path latency budgets against an already-seeded representative
fixture. Seeding and timing stay in separate modules/functions on purpose (per the ST-09
plan) — `scale_fixture.build_representative_fixture` only ever seeds; nothing here creates
data, so a slow seed pass can never be misread as a slow product measurement.

Budgets (fixed by the ST-09 plan, p95 over 20 samples after 2 warmups):

- authenticated bootstrap REST calls together: <= 2.0s
- search: <= 300ms
- first activity page: <= 300ms
- table / Kanban / timeline projections: <= 750ms each

Only sanitized metadata may ever be persisted from this module's results: OS/architecture/
Python version, fixture counts, timings, and pass/fail — never machine paths, the bearer
token, titles/bodies, evidence content, or identifiers.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

import httpx

WARMUP_SAMPLES = 2
TIMED_SAMPLES = 20

BOOTSTRAP_P95_BUDGET_SECONDS = 2.0
SEARCH_P95_BUDGET_SECONDS = 0.3
ACTIVITY_PAGE_P95_BUDGET_SECONDS = 0.3
TABLE_P95_BUDGET_SECONDS = 0.75
KANBAN_P95_BUDGET_SECONDS = 0.75
TIMELINE_P95_BUDGET_SECONDS = 0.75


def percentile_95(samples: list[float]) -> float:
    if not samples:
        raise ValueError("percentile_95 requires at least one sample")
    ordered = sorted(samples)
    # Nearest-rank method: the smallest value at or above the 95th percentile rank.
    rank = math.ceil(0.95 * len(ordered))
    return ordered[min(rank, len(ordered)) - 1]


@dataclass(frozen=True)
class BudgetResult:
    name: str
    p95_seconds: float
    budget_seconds: float

    @property
    def passed(self) -> bool:
        return self.p95_seconds <= self.budget_seconds


@dataclass(frozen=True)
class BackendPerformanceReport:
    results: tuple[BudgetResult, ...]

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.results)

    def as_sanitized_dict(self) -> dict[str, object]:
        return {
            result.name: {
                "p95_seconds": round(result.p95_seconds, 4),
                "budget_seconds": result.budget_seconds,
                "passed": result.passed,
            }
            for result in self.results
        }


def _timed(action) -> float:
    started = time.monotonic()
    action()
    return time.monotonic() - started


def _sample(client: httpx.Client, workspace_id: str, action_name: str) -> float:
    if action_name == "bootstrap":

        def action() -> None:
            client.get("/workspace").raise_for_status()
            client.get("/nodes", params={"workspace_id": workspace_id}).raise_for_status()
            client.get("/edges", params={"workspace_id": workspace_id}).raise_for_status()
            client.get("/canvases", params={"workspace_id": workspace_id}).raise_for_status()
            client.get("/resources", params={"workspace_id": workspace_id}).raise_for_status()

    elif action_name == "search":

        def action() -> None:
            client.get(
                "/search", params={"workspace_id": workspace_id, "q": "fixture"}
            ).raise_for_status()

    elif action_name == "activity_page":

        def action() -> None:
            client.get(
                "/activity-events", params={"workspace_id": workspace_id, "limit": 50}
            ).raise_for_status()

    elif action_name == "table":

        def action() -> None:
            client.post("/views/table", json={"workspace_id": workspace_id}).raise_for_status()

    elif action_name == "kanban":

        def action() -> None:
            client.post("/views/kanban", json={"workspace_id": workspace_id}).raise_for_status()

    elif action_name == "timeline":

        def action() -> None:
            client.post("/views/timeline", json={"workspace_id": workspace_id}).raise_for_status()

    else:
        raise ValueError(f"Unknown performance action: {action_name}")

    return _timed(action)


def measure_backend_budgets(
    base_url: str, token: str, workspace_id: str
) -> BackendPerformanceReport:
    budgets = (
        ("bootstrap", BOOTSTRAP_P95_BUDGET_SECONDS),
        ("search", SEARCH_P95_BUDGET_SECONDS),
        ("activity_page", ACTIVITY_PAGE_P95_BUDGET_SECONDS),
        ("table", TABLE_P95_BUDGET_SECONDS),
        ("kanban", KANBAN_P95_BUDGET_SECONDS),
        ("timeline", TIMELINE_P95_BUDGET_SECONDS),
    )
    with httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {token}"}) as client:
        for action_name, _ in budgets:
            for _ in range(WARMUP_SAMPLES):
                _sample(client, workspace_id, action_name)

        results = []
        for action_name, budget_seconds in budgets:
            samples = [_sample(client, workspace_id, action_name) for _ in range(TIMED_SAMPLES)]
            results.append(BudgetResult(action_name, percentile_95(samples), budget_seconds))

    return BackendPerformanceReport(results=tuple(results))
