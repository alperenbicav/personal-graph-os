"""Bounded MCP operation telemetry (06.5, `WORK.md`).

Records only what is needed to observe protocol health: the tool/resource name, whether
it succeeded, and how long it took. Never logs arguments, returned content, actor names,
request/entity ids, storage paths, or raw exception text — those may carry user-authored
or otherwise sensitive data, and the point of this module is to make that structurally
impossible rather than to rely on callers redacting it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar

_LOGGER = logging.getLogger("personal_graph_os.mcp.telemetry")

_T = TypeVar("_T")


@dataclass
class OperationCounters:
    """In-process counters keyed by (operation, name, ok); read via `snapshot()` in tests."""

    _counts: dict[tuple[str, str, bool], int] = field(default_factory=dict)

    def record(self, operation: str, name: str, ok: bool) -> None:
        key = (operation, name, ok)
        self._counts[key] = self._counts.get(key, 0) + 1

    def snapshot(self) -> dict[tuple[str, str, bool], int]:
        return dict(self._counts)


async def record_operation(
    counters: OperationCounters,
    operation: str,
    name: str,
    call: Callable[[], Awaitable[_T]],
) -> _T:
    started_at = time.perf_counter()
    ok = True
    try:
        return await call()
    except BaseException:
        # `asyncio.CancelledError` is a `BaseException`, not an `Exception` (ST06-F06): a
        # bare `except Exception` here would miss it, leaving `ok=True` for an operation that
        # never completed. Every non-success outcome, cancellation included, is counted and
        # logged as such; the exception is always re-raised, never swallowed.
        ok = False
        raise
    finally:
        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        counters.record(operation, name, ok)
        _LOGGER.info(
            "mcp_operation",
            extra={"operation": operation, "name": name, "ok": ok, "duration_ms": duration_ms},
        )
