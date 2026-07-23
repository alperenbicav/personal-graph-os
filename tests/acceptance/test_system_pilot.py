"""ST09-F01 regression: `scripts/system_pilot.py`'s `main()` must propagate the actual
phase outcome into its printed message and exit code, not unconditionally report `pass`/0.
Also covers the exception path and spawned-process/workspace cleanup.

Loaded by file path (not as a package import) since `scripts/` is a plain CLI-script
directory, not a distributed package (see `pyproject.toml`'s
`[tool.hatch.build.targets.wheel]`); each test gets a fresh module instance so
monkeypatching one test's phase runner can never leak into another.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "system_pilot.py"


_MODULE_NAME = "system_pilot_under_test"


def _load_system_pilot_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # `@dataclass` resolves string annotations via `sys.modules[cls.__module__]`, so the
    # module must be registered there before `exec_module` runs the class body.
    sys.modules[_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[_MODULE_NAME]
        raise
    return module


@pytest.fixture
def system_pilot() -> Iterator[ModuleType]:
    module = _load_system_pilot_module()
    try:
        yield module
    finally:
        del sys.modules[_MODULE_NAME]


class _FakeProcess:
    """Stands in for `subprocess.Popen` so cleanup tests never spawn a real server."""

    def __init__(self) -> None:
        self.terminated = False
        self.waited = False

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> None:
        del timeout
        self.waited = True


def _patch_self_contained_phase(
    system_pilot: ModuleType, monkeypatch: pytest.MonkeyPatch, outcome
) -> None:
    """`accessibility` never builds the frontend or spawns `main()`'s own server, so it's
    the cheapest phase to drive `main()`'s outcome-propagation branches with."""
    if isinstance(outcome, Exception):

        def _raise(*_args, **_kwargs):
            raise outcome

        monkeypatch.setattr(system_pilot, "_run_accessibility_phase", _raise)
    else:
        monkeypatch.setattr(system_pilot, "_run_accessibility_phase", lambda: outcome)


def test_main_reports_pass_and_exits_zero_when_the_phase_passes(
    system_pilot: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_self_contained_phase(
        system_pilot, monkeypatch, system_pilot.PhaseOutcome(passed=True, fixture_counts={})
    )

    exit_code = system_pilot.main(["--phase", "accessibility", "--skip-frontend-build"])

    assert exit_code == 0
    assert "accessibility: pass" in capsys.readouterr().out


def test_main_reports_fail_and_exits_nonzero_when_the_phase_fails(
    system_pilot: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_self_contained_phase(
        system_pilot, monkeypatch, system_pilot.PhaseOutcome(passed=False, fixture_counts={})
    )

    exit_code = system_pilot.main(["--phase", "accessibility", "--skip-frontend-build"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "pass" not in captured.out
    assert "accessibility: fail" in captured.err


def test_main_reports_fail_and_exits_nonzero_when_the_phase_raises(
    system_pilot: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_self_contained_phase(system_pilot, monkeypatch, RuntimeError("boom"))

    exit_code = system_pilot.main(["--phase", "accessibility", "--skip-frontend-build"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "accessibility: fail after" in captured.err
    assert "boom" in captured.err


def test_main_writes_a_failing_report_when_the_phase_fails(
    system_pilot: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    written_outcomes: list[object] = []
    real_write_report = system_pilot._write_report

    def _spy_write_report(*, phase: str, outcome, elapsed_seconds: float):
        written_outcomes.append(outcome)
        return real_write_report(phase=phase, outcome=outcome, elapsed_seconds=elapsed_seconds)

    _patch_self_contained_phase(
        system_pilot, monkeypatch, system_pilot.PhaseOutcome(passed=False, fixture_counts={})
    )
    monkeypatch.setattr(system_pilot, "_write_report", _spy_write_report)

    system_pilot.main(["--phase", "accessibility", "--skip-frontend-build"])

    assert len(written_outcomes) == 1
    (outcome,) = written_outcomes
    assert getattr(outcome, "passed") is False  # noqa: B009 -- dynamically loaded module type


def test_main_terminates_the_spawned_server_and_removes_the_workspace_on_success(
    system_pilot: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_process = _FakeProcess()
    removed_paths: list[Path] = []

    # `_node_version()` (called from `_write_report`) shells out to `node --version` via
    # `subprocess.run`, which internally calls the same `subprocess.Popen` this test fakes
    # out for `main()`'s own server-spawn call — stub it directly rather than let it hit
    # the fake process too.
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: fake_process)
    monkeypatch.setattr(system_pilot, "_node_version", lambda: "v0.0.0")
    monkeypatch.setattr(system_pilot, "_wait_for_server", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(system_pilot, "_read_token", lambda *_args, **_kwargs: "fake-token")
    # `_PHASE_RUNNERS` captures a direct reference to `_run_functional_phase` at module load
    # time, so patching the plain module attribute would not affect the dict lookup `_run_phase`
    # actually uses — patch the dict entry itself instead.
    monkeypatch.setitem(
        system_pilot._PHASE_RUNNERS,
        "functional",
        lambda *_args, **_kwargs: system_pilot.PhaseOutcome(passed=True, fixture_counts={}),
    )
    monkeypatch.setattr(
        shutil, "rmtree", lambda path, ignore_errors=False: removed_paths.append(Path(path))
    )

    exit_code = system_pilot.main(["--phase", "functional", "--skip-frontend-build"])

    assert exit_code == 0
    assert fake_process.terminated is True
    assert len(removed_paths) == 1


def test_main_terminates_the_spawned_server_and_removes_the_workspace_on_failure(
    system_pilot: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_process = _FakeProcess()
    removed_paths: list[Path] = []

    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: fake_process)
    monkeypatch.setattr(system_pilot, "_node_version", lambda: "v0.0.0")
    monkeypatch.setattr(system_pilot, "_wait_for_server", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(system_pilot, "_read_token", lambda *_args, **_kwargs: "fake-token")

    def _raise(*_args, **_kwargs):
        raise RuntimeError("simulated functional-phase failure")

    monkeypatch.setitem(system_pilot._PHASE_RUNNERS, "functional", _raise)
    monkeypatch.setattr(
        shutil, "rmtree", lambda path, ignore_errors=False: removed_paths.append(Path(path))
    )

    exit_code = system_pilot.main(["--phase", "functional", "--skip-frontend-build"])

    assert exit_code == 1
    assert fake_process.terminated is True
    assert len(removed_paths) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__]))
