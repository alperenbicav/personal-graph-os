# System acceptance pilot runbook

ST-09's single repeatable desktop release gate: proves the MVP is keyboard-usable, responsive
at representative personal scale, and recoverable — all locally, against the real packaged
server/build, using only synthetic data. This runbook does not require, and should never be
run against, real personal or customer data (see "Known MVP limits" below).

## Prerequisites

- A clean working tree on the branch you intend to gate (uncommitted changes are fine to test
  locally, but the gate reports against whatever is currently on disk).
- `uv` and `npm` installed and resolvable; `frontend/`'s dependencies installed
  (`npm install` under `frontend/` — this also pins the Playwright/axe browser toolchain used
  by the `accessibility`/`performance`/`recovery` phases).
- Playwright's Chromium browser installed once: `npx playwright install chromium` from
  `frontend/` (the `accessibility` phase and `performance`/`recovery`'s browser-driven checks
  both need it).
- No other process already bound to the port the harness will pick (it always asks the OS for
  a free one — see "Known MVP limits" for the one phase that does not).

## One-command automated gate

`scripts/system_pilot.py` is the single documented harness command. It always builds the real
frontend (unless told to skip), spawns/manages whatever real server(s) a phase needs against a
fresh private temporary workspace, runs one phase, writes a sanitized report to
`.system-pilot-reports/` (gitignored), and always cleans up — success or failure.

```
uv run python scripts/system_pilot.py --phase functional
uv run python scripts/system_pilot.py --phase functional --with-representative-fixture
uv run python scripts/system_pilot.py --phase accessibility --skip-frontend-build
uv run python scripts/system_pilot.py --phase performance --skip-frontend-build
uv run python scripts/system_pilot.py --phase recovery --skip-frontend-build
```

Run `functional` first (it builds the frontend fresh); pass `--skip-frontend-build` on the
phases you run afterward in the same session to avoid rebuilding every time. The full gate for
a release is all four phases passing, with `performance` run twice consecutively (see below).
`accessibility`'s own `update-prompt` test always triggers two additional internal rebuilds of
its own (real second-service-worker-version proof, ST09-F05) regardless of
`--skip-frontend-build` — expect that phase to take longer and to leave the canonical
marker-free build behind afterward.

| Phase | What it proves | Backing test/tool |
|---|---|---|
| `functional` | Every ST-09 product invariant (custom schema, connected nodes, canvases, research/discovery, attachment/reference evidence, saved views, activity/undo, an MCP mutation with idempotency, a Context Pack, and a portable export) via real REST/MCP calls | `tests/acceptance/functional_fixture.py` |
| `accessibility` | No serious/critical automated axe violation across every listed desktop state at both required viewports (including a real second-service-worker-version `update-prompt` scan), plus a real no-pointer keyboard walkthrough | `frontend/e2e/accessibility.spec.ts` |
| `performance` | Bounded single-user REST/browser latency budgets on the fixed representative dataset | `tests/acceptance/performance_fixture.py`, `frontend/e2e/performance-check.mjs` |
| `recovery` | A full stop/backup/restore/restart round trip preserves canonical state, managed-file bytes, rebuilt search, MCP idempotency replay, and Context Pack materialization; a corrupted backup fails closed | `tests/acceptance/test_recovery_drill.py` |

Each phase prints `<phase>: pass (<elapsed>s) -> <report path>` on success, or
`<phase>: fail after <elapsed>s: <error>` (exit code 1) on failure — a failed run still writes
a report with `"outcome": "fail"`.

## Manual keyboard smoke checklist

The automated `accessibility` phase already proves this end to end, but for a quick manual
spot-check before a release (no representative-scale data needed):

1. Load `/app/`, confirm the token field is focused on load; type the token, Tab to Unlock,
   Enter.
2. Tab to the capture field, type a title, Enter — confirm it appears on the canvas.
3. Tab into the canvas's object list (a real listbox, not the pointer-only canvas), arrow
   between entries, Enter to select — confirm the Inspector updates.
4. In the Inspector, reach the status `<select>` by keyboard and change it with arrow keys.
5. Open Schema (button), confirm focus moves inside the dialog and Escape closes it and returns
   focus to the Schema button.
6. Switch views with Tab/Enter through the nav tabs (Table/Kanban/Timeline/Search/Research/
   Discovery/Activity) — confirm each renders and is keyboard-reachable.
7. In Activity, expand the most recent event by keyboard, Undo it, confirm the row-inline
   "Undone." status appears.
8. Trigger connect (real pointer drag between two canvas node handles is currently the only
   way to open the connect dialog — see "Known MVP limits"), confirm modal focus/Escape/restore
   behave the same as Schema's.

## Performance-budget interpretation

`performance`'s report `fixture_counts.backend_budgets` has one entry per budget:
`{"p95_seconds": ..., "budget_seconds": ..., "passed": ...}`; `browser_unlock_to_controls_seconds`
and `browser_view_switch_seconds` are the two browser-side numbers. A release-gating run
requires **two consecutive passing runs** of `--phase performance` on the machine you consider
the reference environment — this is a manual re-run, not something the phase loops internally,
so a single lucky pass on a busy machine can't gate a release. If either run fails:

1. Re-run once more before investigating — a single miss on a loaded laptop is common noise.
2. If it fails consistently, check what regressed: a new N+1 query (compare against
   `tests/acceptance/test_query_plans.py`'s asserted index usage), a newly-unmemoized React
   render on a hot view, or a genuinely larger per-request payload.
3. Budgets themselves are fixed by the ST-09 plan (`.ai/work/active/EP-2026-002-personal-graph-os-mvp/WORK.md`)
   and are not to be loosened locally to make a run pass; a real budget-change proposal goes
   back through planning, not through this runbook.

## Recovery steps (what the `recovery` phase automates, if you ever need it by hand)

1. Stop the running server (`Ctrl-C` the `personal_graph_os.api` process, or send `SIGTERM`).
2. `uv run pgos-backup create <workspace_dir> <output_dir>` — refuses if any managed-file
   operation is still unreconciled; wait and retry rather than forcing it.
3. `uv run pgos-backup verify <backup_path>` before trusting an archive you didn't just create
   (e.g. one pulled from off-machine storage).
4. Restore only into a **fresh, empty** directory — never the original workspace directory,
   and never a non-empty one: `uv run pgos-backup restore <backup_path> <fresh_empty_dir>`.
5. Start the server against the restored directory
   (`PGOS_DB_PATH=<fresh_empty_dir>/graph.db uv run python -m personal_graph_os.api`); it
   generates a brand-new bearer token on this first boot (the token file is deliberately never
   restored) — retrieve it with `uv run pgos-token show` using the same `PGOS_DB_PATH`.
6. Confirm canonical data (nodes/edges/canvases/resources/activity) is visible through the UI
   and/or REST before treating the restore as complete.

## Known MVP limits

- No mobile/tablet/touch acceptance; desktop only, both required viewports.
- No multi-user, load, or stress testing; every phase is single-client and synthetic.
- No real personal/customer data is used or required by any phase; do not point any phase at a
  workspace containing real data.
- The connect-two-objects dialog can currently only be opened by a real pointer drag between
  two canvas node handles — there is no keyboard-only way to create an edge yet. The dialog
  itself is fully keyboard-operable once open (focus trap, Escape, restore); only the
  drag-to-open gesture is pointer-only.
- `performance`'s dynamically-chosen port and `accessibility`/`recovery`'s independently
  managed servers are the only ports this harness opens; nothing here binds to a non-loopback
  address.

## Artifact cleanup

Every phase cleans up on both success and failure: spawned server processes are terminated,
temporary workspace/backup/restore directories are removed, and generated backup archives are
deleted. `.system-pilot-reports/` itself is gitignored but not auto-pruned — remove it
(`rm -rf .system-pilot-reports`) between runs if you don't want old reports lying around; they
contain no sensitive content, only sanitized environment/count/timing metadata, but there is
no reason to keep them once read. If a run is interrupted (killed, machine sleep) rather than
completing normally, check for and remove any leftover `pgos-*-workspace-*`/`pgos-*-staging-*`
directories under your OS temp directory — the harness's own `finally` blocks only run on a
clean interpreter exit.

## Issue evidence rules

When a phase fails and you need to report or investigate it:

- Keep the phase's own sanitized JSON report (`.system-pilot-reports/<phase>-<timestamp>.json`)
  — it never contains paths, the bearer token, titles/bodies, or other fixture content, so it
  is safe to attach to an issue as-is. The stack trace/error message printed to stderr is also
  generally safe to keep locally, but the surrounding process/Playwright output can
  occasionally echo generated fixture content in traceback locals — read it before attaching
  it anywhere, and redact if unsure.
- Never attach `.system-pilot-reports/*.json` alongside a raw Playwright trace
  (`test-results/**/trace.zip`) without opening the trace first: traces are real recorded
  network/DOM state and, unlike the sanitized report, are not path/token-free by construction.
- If the failure looks environment-specific (a performance-budget miss, a flaky browser
  connect gesture), record the exact command and the two consecutive-run results before
  concluding it's a real regression — see "Performance-budget interpretation" above.
