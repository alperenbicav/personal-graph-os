from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from personal_graph_os.domain.agents import Agent, AgentRun, AgentRunStatus, AgentWriteMode
from personal_graph_os.domain.identifiers import AgentId, new_id
from personal_graph_os.infrastructure.sqlite.connection import open_connection
from personal_graph_os.infrastructure.sqlite.migrations.runner import run_migrations
from personal_graph_os.infrastructure.sqlite.repositories import SqliteAgentRepository


@pytest.fixture
def connection(tmp_path) -> sqlite3.Connection:
    db_path = tmp_path / "test-agents.db"
    conn = open_connection(db_path)
    run_migrations(conn)
    return conn


def test_default_seeded_agents_present_after_migration(connection: sqlite3.Connection) -> None:
    repo = SqliteAgentRepository(connection)
    agents = repo.list_agents()
    names = {agent.name for agent in agents}
    assert "Research-Agent" in names
    assert "Ingest-Agent" in names
    assert "Plan-Agent" in names

    research_agent = next(a for a in agents if a.name == "Research-Agent")
    assert research_agent.emoji == "📚"
    assert "Research-Agent" in research_agent.system_prompt
    assert research_agent.write_mode == AgentWriteMode.PROPOSAL


def test_agent_repository_crud(connection: sqlite3.Connection) -> None:
    repo = SqliteAgentRepository(connection)

    agent_id = AgentId(new_id())
    agent = Agent(
        id=agent_id,
        name="Custom-Agent",
        emoji="⚡",
        system_prompt="Custom prompt",
        tool_allowlist=["tool1", "tool2"],
        model="gpt-4o",
        write_mode=AgentWriteMode.DIRECT,
        created_at=datetime.now(UTC),
    )

    repo.save_without_commit(agent)
    connection.commit()

    fetched = repo.get(agent_id)
    assert fetched is not None
    assert fetched.name == "Custom-Agent"
    assert fetched.emoji == "⚡"
    assert fetched.tool_allowlist == ["tool1", "tool2"]
    assert fetched.model == "gpt-4o"
    assert fetched.write_mode == AgentWriteMode.DIRECT

    # Update
    updated = fetched.model_copy(update={"name": "Renamed-Agent", "emoji": "🔥"})
    repo.save_without_commit(updated)
    connection.commit()

    re_fetched = repo.get(agent_id)
    assert re_fetched is not None
    assert re_fetched.name == "Renamed-Agent"
    assert re_fetched.emoji == "🔥"

    # Delete
    repo.delete_without_commit(agent_id)
    connection.commit()
    assert repo.get(agent_id) is None


def test_agent_runs_recorded_and_listed(connection: sqlite3.Connection) -> None:
    repo = SqliteAgentRepository(connection)
    agents = repo.list_agents()
    agent = agents[0]

    run1 = AgentRun.create(
        agent_id=agent.id,
        action="message",
        summary="First test response",
        status=AgentRunStatus.APPLIED,
    )
    repo.record_run_without_commit(run1)

    run2 = AgentRun.create(
        agent_id=agent.id,
        action="proposal",
        summary="Proposed edge connection",
        status=AgentRunStatus.PENDING_REVIEW,
        diff_json='{"action": "create_edge"}',
    )
    repo.record_run_without_commit(run2)
    connection.commit()

    runs = repo.list_runs_for_agent(agent.id)
    assert len(runs) >= 2
    summaries = [r.summary for r in runs]
    assert "First test response" in summaries
    assert "Proposed edge connection" in summaries

    fetched_run = repo.get_run(run2.id)
    assert fetched_run is not None
    assert fetched_run.diff_json == '{"action": "create_edge"}'
    assert fetched_run.status == AgentRunStatus.PENDING_REVIEW
