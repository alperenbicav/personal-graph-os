"""ST06-F06 re-review: a real client cancellation must be able to interrupt an in-progress
MCP mutation, not just be recorded as non-success after the fact. `_run_gateway_call` is the
exact boundary a real MCP client's cancelled `call_tool`/`read_resource` request runs through
(`build_mcp_server` wraps every handler call with it), so exercising it directly with genuine
asyncio task cancellation -- synchronized deterministically against real in-flight SQLite
execution, not a bare pre-mutation `CancelledError` -- is a faithful proof of the same boundary
a real client hits, without the unrelated complexity of tearing down a live HTTP connection
mid-request.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading

import anyio
import pytest

from personal_graph_os.infrastructure.mcp.server import _run_gateway_call


class _ObservableConnection(sqlite3.Connection):
    """Signals exactly when `interrupt()` actually ran, so a test can prove the interrupt
    happened while the operation was still genuinely in flight (inside the SQLite VM, paused
    in a user-defined-function callback) rather than racing an unsynchronized assertion."""

    def interrupt(self) -> None:
        super().interrupt()
        self.interrupt_called.set()  # type: ignore[attr-defined]


def test_cancelling_an_in_progress_call_interrupts_it_rolls_back_and_frees_the_lock() -> None:
    connection = sqlite3.connect(":memory:", check_same_thread=False, factory=_ObservableConnection)
    connection.interrupt_called = threading.Event()  # type: ignore[attr-defined]
    connection.execute("CREATE TABLE t (id INTEGER)")
    connection.commit()

    entered = threading.Event()
    release = threading.Event()

    def _block_inside_the_query() -> int:
        # Invoked BY the SQLite VM as part of executing the SELECT below: while blocked here,
        # the VM itself is paused mid-statement, so `connection.interrupt()` (called from the
        # cancelling asyncio task, on a different thread) targets a genuinely in-flight
        # operation, exactly like a real client cancelling mid-mutation.
        entered.set()
        release.wait(5.0)
        return 0

    connection.create_function("block_inside_the_query", 0, _block_inside_the_query)

    def mutate() -> dict[str, object]:
        connection.execute("SELECT block_inside_the_query()").fetchone()
        connection.execute("INSERT INTO t (id) VALUES (1)")
        connection.commit()
        return {"ok": True}

    lock = anyio.Lock()

    async def scenario() -> None:
        async def call() -> dict[str, object]:
            return await _run_gateway_call(lock, connection, mutate)

        task = asyncio.ensure_future(call())
        await asyncio.to_thread(entered.wait, 5.0)
        assert entered.is_set(), "the mutation never reached its in-flight synchronization point"

        task.cancel()
        # Wait for proof that `connection.interrupt()` actually ran while the VM was still
        # paused inside `_block_inside_the_query`, before letting that callback return.
        await asyncio.to_thread(connection.interrupt_called.wait, 5.0)  # type: ignore[attr-defined]
        assert connection.interrupt_called.is_set()  # type: ignore[attr-defined]
        release.set()

        with pytest.raises(asyncio.CancelledError):
            await task

        # No partial state: the INSERT never ran because the interrupted SELECT raised first.
        row_count = connection.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        assert row_count == 0

        # The lock was released cleanly -- the next request is not blocked by the cancelled one.
        async with lock:
            pass

    asyncio.run(scenario())


def test_cancelling_a_call_outside_sqlite_holds_the_lock_until_the_worker_finishes() -> None:
    """ST06-F06: `connection.interrupt()` only aborts a statement genuinely executing inside
    SQLite. A worker paused in ordinary Python (already past its SQL statements, about to
    commit) is untouched by `interrupt()` and keeps running after cancellation is requested.
    The lock must stay held -- and the connection must stay untouched by anything else --
    until that worker actually finishes, even though `interrupt()` had no effect on it and no
    fixed wait elapses in between.
    """
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.execute("CREATE TABLE t (id INTEGER)")
    connection.commit()

    reached_outside_sqlite = threading.Event()
    release = threading.Event()

    def mutate() -> dict[str, object]:
        # Past all SQL statements already -- interrupt() cannot touch this worker here.
        reached_outside_sqlite.set()
        release.wait(5.0)
        connection.execute("INSERT INTO t (id) VALUES (1)")
        connection.commit()
        return {"ok": True}

    lock = anyio.Lock()

    async def scenario() -> None:
        async def call() -> dict[str, object]:
            return await _run_gateway_call(lock, connection, mutate)

        task = asyncio.ensure_future(call())
        await asyncio.to_thread(reached_outside_sqlite.wait, 5.0)
        assert reached_outside_sqlite.is_set()

        task.cancel()
        # Give the cancellation machinery a chance to run; the worker is still parked on
        # `release`, well outside SQLite, so `interrupt()` is a no-op and the worker has not
        # finished. The lock must still be held -- proven by a concurrent acquire attempt
        # that has not completed while the worker remains alive.
        await asyncio.sleep(0.2)

        async def acquire_and_release() -> None:
            async with lock:
                pass

        lock_probe = asyncio.ensure_future(acquire_and_release())
        await asyncio.sleep(0.2)
        assert not lock_probe.done(), "the lock was released before the worker finished"

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Now that the worker has genuinely finished, the lock becomes acquirable and the
        # worker's own commit -- which ran after the cancellation was requested -- is visible.
        await lock_probe
        row_count = connection.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        assert row_count == 1

        async with lock:
            pass

    asyncio.run(scenario())


def test_a_normal_completed_call_is_unaffected_by_the_cancellation_machinery() -> None:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.execute("CREATE TABLE t (id INTEGER)")
    connection.commit()

    def mutate() -> dict[str, object]:
        connection.execute("INSERT INTO t (id) VALUES (1)")
        connection.commit()
        return {"ok": True}

    lock = anyio.Lock()

    async def scenario() -> None:
        result = await _run_gateway_call(lock, connection, mutate)
        assert result == {"ok": True}

    asyncio.run(scenario())
    row_count = connection.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    assert row_count == 1


def test_a_raised_gateway_error_during_a_call_still_propagates_normally() -> None:
    connection = sqlite3.connect(":memory:", check_same_thread=False)

    def mutate() -> dict[str, object]:
        raise ValueError("boom")

    lock = anyio.Lock()

    async def scenario() -> None:
        with pytest.raises(ValueError, match="boom"):
            await _run_gateway_call(lock, connection, mutate)

    asyncio.run(scenario())
