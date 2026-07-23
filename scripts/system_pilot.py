"""ST-09 system-acceptance harness: the one documented local command that builds the real
frontend, spawns the real packaged server against a fresh private temporary workspace, runs
one acceptance phase against it, and always cleans up afterward — success or failure.

Usage (from the repository root):

    uv run python scripts/system_pilot.py --phase functional
    uv run python scripts/system_pilot.py --phase functional --with-representative-fixture
    uv run python scripts/system_pilot.py --phase functional --skip-frontend-build

Phases:

- `functional` (09.1, implemented here): builds the small functional fixture described in
  `.ai/work/active/EP-2026-002-personal-graph-os-mvp/WORK.md`'s ST-09 plan against a real
  running server. Pass `--with-representative-fixture` to also seed (but not time) the
  fixed 2,000-node representative dataset, proving the seed path works end to end; this is
  still opt-in and separate from the pytest `slow` marker.
- `performance` (09.3, implemented here): seeds the fixed representative dataset, measures
  backend REST-path p95 latency budgets (bootstrap/search/activity-page/table/kanban/
  timeline) after two warmups over twenty samples, then drives a real headless browser
  (`e2e/performance-check.mjs`, not a `playwright test` file — see its own docstring for
  why) against the same running, seeded server using the same two-warmup/twenty-sample/p95
  methodology to measure unlock-to-primary-controls and per-projection switch time. The
  bearer token is piped to the browser check over stdin, never passed as a command-line
  argument (ST09-F06). Every budget is fixed by
  `.ai/work/active/EP-2026-002-personal-graph-os-mvp/WORK.md`'s ST-09 plan; only sanitized
  counts/p95/budget/pass-fail are ever recorded.
- `accessibility` (09.2, implemented here): shells out to the frontend's own real Playwright
  run scoped to `e2e/accessibility.spec.ts` (`npm run test:e2e -- e2e/accessibility.spec.ts`),
  which manages its own server via `frontend/e2e/global-setup.ts` (a fixed port, separate
  from this script's own dynamically-chosen one) — so this phase does NOT also spawn this
  script's usual Python server; nothing here would use it, and skipping it removes any port
  collision risk entirely rather than merely making it unlikely. Only pass/fail and elapsed
  time are recorded; no titles/paths/tokens. Any `test-results/` trace this run produced is
  always removed afterward, pass or fail (ST09-F06) — traces are real recorded network
  captures and are not token-free the way this harness's own reports are.
- `recovery` (09.4, implemented here): runs both halves of the slice. First
  `tests/acceptance/test_recovery_drill.py` (`uv run pytest -q -m slow`), which seeds a real
  workspace, stops it, backs it up, restores into a fresh empty directory, starts a second
  real server against the restored database with a freshly generated token, and compares
  canonical state/managed-file hashes/rebuilt search/MCP idempotency replay/Context Pack
  materialization between the two. Then `e2e/pilot.spec.ts` (`npm run test:e2e --
  e2e/pilot.spec.ts`, ST09-F02) — the complete UI pilot journey (custom schema, confirmed
  connect, place, edit, files, discovery, activity/undo, export) plus its own real
  stop/backup/restore/restart, re-verified through the UI at both required viewports, not
  just REST/MCP. Like `accessibility`, both manage their own server(s) end to end and do not
  use this script's own spawned Python server. Only pass/fail (per half) and elapsed time
  are recorded.

The report this command writes goes to `.system-pilot-reports/` (gitignored) and records
only environment/count/timing/outcome metadata: OS, architecture, Python/Node versions,
fixture counts, elapsed seconds, and pass/fail. It never records machine paths, the bearer
token, titles/bodies, evidence content, or other identifiers.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = REPO_ROOT / "frontend"
FRONTEND_DIST = FRONTEND_DIR / "dist"
REPORTS_DIR = REPO_ROOT / ".system-pilot-reports"

_PHASES = ("functional", "accessibility", "performance", "recovery")


@dataclass(frozen=True)
class PhaseOutcome:
    passed: bool
    fixture_counts: dict[str, object]


def _build_frontend() -> None:
    subprocess.run(["npm", "run", "build"], cwd=FRONTEND_DIR, check=True)


def _node_version() -> str | None:
    try:
        result = subprocess.run(["node", "--version"], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def _wait_for_server(url: str, timeout_seconds: float = 20.0) -> None:
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:  # noqa: S310
                if response.status < 500:
                    return
        except (urllib.error.URLError, ConnectionError):
            pass
        time.sleep(0.2)
    raise RuntimeError(f"server at {url} did not become ready within {timeout_seconds}s")


def _read_token(token_path: Path, timeout_seconds: float = 5.0) -> str:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if token_path.exists():
            token = token_path.read_text(encoding="utf-8").strip()
            if token:
                return token
        time.sleep(0.1)
    raise RuntimeError(f"bearer token file never appeared at {token_path}")


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run_functional_phase(
    base_url: str, mcp_url: str, token: str, *, with_representative_fixture: bool
) -> PhaseOutcome:
    sys.path.insert(0, str(REPO_ROOT))
    from tests.acceptance.functional_fixture import build_functional_fixture

    functional_summary = build_functional_fixture(base_url, mcp_url, token)
    counts: dict[str, object] = {
        "functional_node_count": functional_summary.node_count,
        "functional_edge_count": functional_summary.edge_count,
        "functional_canvas_count": functional_summary.canvas_count,
        "functional_placement_count": functional_summary.placement_count,
        "functional_resource_count": functional_summary.resource_count,
        "functional_discovery_run_id_present": bool(functional_summary.discovery_run_id),
        "functional_attachment_count": functional_summary.attachment_count,
        "functional_file_reference_count": functional_summary.file_reference_count,
        "functional_saved_view_count": functional_summary.saved_view_count,
        "functional_activity_event_count": functional_summary.activity_event_count,
        "functional_context_pack_count": functional_summary.context_pack_count,
        "functional_mcp_mutation_replayed": functional_summary.mcp_mutation_replayed,
    }

    if with_representative_fixture:
        from tests.acceptance.scale_fixture import build_representative_fixture

        scale_summary = build_representative_fixture(base_url, token)
        counts.update(
            {
                "representative_node_count": scale_summary.node_count,
                "representative_edge_count": scale_summary.edge_count,
                "representative_resource_count": scale_summary.resource_count,
                "representative_active_placement_count": (
                    scale_summary.active_canvas_placement_count
                ),
                "representative_activity_event_count": scale_summary.activity_event_count,
                "representative_attachment_count": scale_summary.attachment_count,
            }
        )

    return PhaseOutcome(passed=True, fixture_counts=counts)


def _run_performance_phase(
    base_url: str, mcp_url: str, token: str, *, with_representative_fixture: bool
) -> PhaseOutcome:
    del mcp_url, with_representative_fixture  # unused: performance always seeds its own fixture
    sys.path.insert(0, str(REPO_ROOT))
    from tests.acceptance.performance_fixture import measure_backend_budgets
    from tests.acceptance.scale_fixture import build_representative_fixture

    scale_summary = build_representative_fixture(base_url, token)
    backend_report = measure_backend_budgets(base_url, token, scale_summary.workspace_id)

    browser_script = REPO_ROOT / "frontend" / "e2e" / "performance-check.mjs"
    # ST09-F06: the token is piped over stdin, never as a command-line argument — a local
    # `ps`/process listing must never expose it. This script is a plain Node script, not a
    # `playwright test` file, so it never produces a `test-results/` trace either.
    browser_result = subprocess.run(
        ["node", str(browser_script), base_url],
        cwd=FRONTEND_DIR,
        input=token,
        capture_output=True,
        text=True,
    )
    try:
        browser_metrics = json.loads(browser_result.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as error:
        raise RuntimeError(
            f"browser performance check produced no parseable result: {browser_result.stderr}"
        ) from error
    browser_passed = browser_result.returncode == 0

    counts: dict[str, object] = {
        "representative_node_count": scale_summary.node_count,
        "representative_edge_count": scale_summary.edge_count,
        "representative_resource_count": scale_summary.resource_count,
        "representative_active_placement_count": scale_summary.active_canvas_placement_count,
        "representative_activity_event_count": scale_summary.activity_event_count,
        "representative_attachment_count": scale_summary.attachment_count,
        "backend_budgets": backend_report.as_sanitized_dict(),
        "browser_budgets": browser_metrics,
    }

    return PhaseOutcome(passed=backend_report.passed and browser_passed, fixture_counts=counts)


def _purge_frontend_test_results() -> None:
    """ST09-F06: a failed `playwright test` run retains `test-results/**/trace.zip` by
    `playwright.config.ts`'s `trace: 'retain-on-failure'` — and a trace is a real recorded
    network capture, so it is NOT token-free the way this harness's own sanitized reports
    are. `system_pilot.py` is the documented release-gate path, not the place to debug a
    failure by hand (see `docs/runbooks/system-pilot.md`'s issue-evidence rules for that) —
    so it always removes any trace this run produced, pass or fail. A developer who needs a
    trace to debug a failure runs `npm run test:e2e` directly instead of through this script.
    """
    shutil.rmtree(FRONTEND_DIR / "test-results", ignore_errors=True)


def _run_accessibility_phase() -> PhaseOutcome:
    """Runs the real `frontend/e2e/accessibility.spec.ts` Playwright suite. This phase owns
    no server of its own: `npm run test:e2e` drives `frontend/e2e/global-setup.ts`, which
    spawns and tears down its own server on its own fixed port, entirely independent of the
    Python server `main()` would otherwise spawn — so `main()` skips spawning that server
    for this phase rather than leaving an unused process that could theoretically collide.
    """
    try:
        result = subprocess.run(
            ["npm", "run", "test:e2e", "--", "e2e/accessibility.spec.ts"],
            cwd=FRONTEND_DIR,
            capture_output=True,
            text=True,
        )
    finally:
        _purge_frontend_test_results()
    if result.returncode != 0:
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
    return PhaseOutcome(passed=result.returncode == 0, fixture_counts={})


def _run_recovery_phase() -> PhaseOutcome:
    """Runs both halves of 09.4: `tests/acceptance/test_recovery_drill.py` (REST/MCP state
    across a real stop/backup/restore/restart) and `e2e/pilot.spec.ts` (ST09-F02 — the
    complete UI pilot journey plus a UI-driven recovery re-verification, at both required
    viewports). Both manage their own real server(s) end to end, entirely independent of the
    Python server `main()` would otherwise spawn, so that server is skipped for this phase
    too (see `_run_accessibility_phase`'s docstring for why)."""
    drill_result = subprocess.run(
        ["uv", "run", "pytest", "-q", "-m", "slow", "tests/acceptance/test_recovery_drill.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if drill_result.returncode != 0:
        print(drill_result.stdout, file=sys.stderr)
        print(drill_result.stderr, file=sys.stderr)

    try:
        pilot_result = subprocess.run(
            ["npm", "run", "test:e2e", "--", "e2e/pilot.spec.ts"],
            cwd=FRONTEND_DIR,
            capture_output=True,
            text=True,
        )
    finally:
        _purge_frontend_test_results()
    if pilot_result.returncode != 0:
        print(pilot_result.stdout, file=sys.stderr)
        print(pilot_result.stderr, file=sys.stderr)

    passed = drill_result.returncode == 0 and pilot_result.returncode == 0
    return PhaseOutcome(
        passed=passed,
        fixture_counts={
            "recovery_drill_passed": drill_result.returncode == 0,
            "ui_pilot_passed": pilot_result.returncode == 0,
        },
    )


_PHASE_RUNNERS: dict[str, Callable[..., PhaseOutcome]] = {
    "functional": _run_functional_phase,
    "performance": _run_performance_phase,
}

# Phases in this set own their own server/environment and must not receive `main()`'s
# spawned Python server (nothing would use it, and running a second, unnecessary server
# alongside a self-contained phase is exactly the redundancy the accessibility phase's own
# docstring rejects, not merely a style preference).
_SELF_CONTAINED_PHASES = frozenset({"accessibility", "recovery"})


def _run_phase(
    phase: str, base_url: str, mcp_url: str, token: str, *, with_representative_fixture: bool
) -> PhaseOutcome:
    if phase == "accessibility":
        return _run_accessibility_phase()
    if phase == "recovery":
        return _run_recovery_phase()
    return _PHASE_RUNNERS[phase](
        base_url, mcp_url, token, with_representative_fixture=with_representative_fixture
    )


def _write_report(*, phase: str, outcome: PhaseOutcome, elapsed_seconds: float) -> Path:
    REPORTS_DIR.mkdir(exist_ok=True)
    report = {
        "phase": phase,
        "outcome": "pass" if outcome.passed else "fail",
        "elapsed_seconds": round(elapsed_seconds, 3),
        "os": platform.system(),
        "architecture": platform.machine(),
        "python_version": platform.python_version(),
        "node_version": _node_version(),
        "fixture_counts": outcome.fixture_counts,
    }
    report_path = REPORTS_DIR / f"{phase}-{int(time.time())}.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=_PHASES, required=True)
    parser.add_argument(
        "--with-representative-fixture",
        action="store_true",
        help="Also seed (not time) the fixed representative-scale dataset (functional phase only).",
    )
    parser.add_argument(
        "--skip-frontend-build",
        action="store_true",
        help="Reuse an existing frontend/dist build instead of rebuilding it.",
    )
    args = parser.parse_args(argv)

    if not args.skip_frontend_build:
        _build_frontend()
    if not FRONTEND_DIST.is_dir():
        raise RuntimeError(
            f"no frontend build found at {FRONTEND_DIST}; remove --skip-frontend-build"
        )

    self_contained = args.phase in _SELF_CONTAINED_PHASES
    server_process: subprocess.Popen | None = None
    workspace_dir: Path | None = None
    base_url = ""

    if not self_contained:
        port = _free_port()
        workspace_dir = Path(tempfile.mkdtemp(prefix="pgos-system-pilot-workspace-"))
        os.chmod(workspace_dir, 0o700)
        database_path = workspace_dir / "graph.db"
        base_url = f"http://127.0.0.1:{port}"

        server_process = subprocess.Popen(
            ["uv", "run", "python", "-m", "personal_graph_os.api"],
            cwd=REPO_ROOT,
            env={
                **os.environ,
                "PGOS_DB_PATH": str(database_path),
                "PGOS_PORT": str(port),
                "PGOS_STATIC_DIR": str(FRONTEND_DIST),
                "PGOS_TRUSTED_HOSTS": "127.0.0.1,localhost",
            },
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    started_at = time.monotonic()
    try:
        token = ""
        if not self_contained:
            assert workspace_dir is not None
            _wait_for_server(f"{base_url}/app/")
            token = _read_token(workspace_dir / "api-token")
        outcome = _run_phase(
            args.phase,
            base_url,
            f"{base_url}/mcp",
            token,
            with_representative_fixture=args.with_representative_fixture,
        )
        elapsed_seconds = time.monotonic() - started_at
        report_path = _write_report(
            phase=args.phase, outcome=outcome, elapsed_seconds=elapsed_seconds
        )
        relative_report_path = report_path.relative_to(REPO_ROOT)
        if outcome.passed:
            print(f"{args.phase}: pass ({elapsed_seconds:.1f}s) -> {relative_report_path}")
            return 0
        print(
            f"{args.phase}: fail ({elapsed_seconds:.1f}s) -> {relative_report_path}",
            file=sys.stderr,
        )
        return 1
    except Exception as error:
        elapsed_seconds = time.monotonic() - started_at
        _write_report(
            phase=args.phase,
            outcome=PhaseOutcome(passed=False, fixture_counts={}),
            elapsed_seconds=elapsed_seconds,
        )
        print(f"{args.phase}: fail after {elapsed_seconds:.1f}s: {error}", file=sys.stderr)
        return 1
    finally:
        if server_process is not None:
            server_process.terminate()
            try:
                server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_process.kill()
                server_process.wait(timeout=5)
        if workspace_dir is not None:
            shutil.rmtree(workspace_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
