"""06.5: bounded MCP operation telemetry — counts and outcomes only, never payloads."""

from __future__ import annotations

import asyncio

import pytest

from personal_graph_os.infrastructure.mcp.telemetry import OperationCounters, record_operation


async def _succeed() -> str:
    return "ok"


async def _fail() -> str:
    raise ValueError("boom")


def test_record_operation_counts_success_and_returns_the_call_result() -> None:
    counters = OperationCounters()

    result = asyncio.run(record_operation(counters, "tool", "pgos_get_workspace", _succeed))

    assert result == "ok"
    assert counters.snapshot() == {("tool", "pgos_get_workspace", True): 1}


def test_record_operation_counts_failure_without_swallowing_the_exception() -> None:
    counters = OperationCounters()

    with pytest.raises(ValueError, match="boom"):
        asyncio.run(record_operation(counters, "tool", "pgos_create_node", _fail))

    assert counters.snapshot() == {("tool", "pgos_create_node", False): 1}


def test_record_operation_accumulates_across_repeated_calls() -> None:
    counters = OperationCounters()

    asyncio.run(record_operation(counters, "tool", "pgos_search", _succeed))
    asyncio.run(record_operation(counters, "tool", "pgos_search", _succeed))
    with pytest.raises(ValueError, match="boom"):
        asyncio.run(record_operation(counters, "tool", "pgos_search", _fail))

    assert counters.snapshot() == {
        ("tool", "pgos_search", True): 2,
        ("tool", "pgos_search", False): 1,
    }
