from __future__ import annotations

import sqlite3

import httpx

from personal_graph_os.application.agent_service import AgentService
from personal_graph_os.application.capture_planning_orchestrator import (
    CapturePlanningOrchestrator,
)
from personal_graph_os.application.capture_service import CaptureService
from personal_graph_os.application.semantic_schema import ensure_semantic_schema
from personal_graph_os.application.services import ResourceService, new_workspace
from personal_graph_os.application.telegram_adapters import (
    TelegramChat,
    TelegramMessage,
    TelegramUpdate,
    TelegramUser,
)
from personal_graph_os.application.telegram_service import (
    TelegramService,
    TelegramUpdateOutcome,
)
from personal_graph_os.domain.identifiers import WorkspaceId
from personal_graph_os.domain.schema import EdgeType, NodeType
from personal_graph_os.infrastructure.llm.openai_provider import OpenAiLlmProvider
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteAgentRepository,
    SqliteIdempotencyReceiptRepository,
    SqliteIngestionJobRepository,
    SqliteResourceRepository,
    SqliteWorkspaceRepository,
)
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import (
    SqliteResearchUnitOfWork,
)
from personal_graph_os.infrastructure.telegram.fake_client import FakeTelegramClient


def _setup_service(
    sqlite_connection: sqlite3.Connection,
    mock_reply: str = "Agent answer text.",
) -> tuple[TelegramService, FakeTelegramClient, SqliteAgentRepository, WorkspaceId]:
    ws_repo = SqliteWorkspaceRepository(sqlite_connection)
    workspace = ensure_semantic_schema(
        new_workspace("Personal").model_copy(
            update={
                "node_types": (
                    NodeType(name="Task"),
                    NodeType(name="Resource", system_key="resource"),
                ),
                "edge_types": (EdgeType(name="relates_to"),),
            }
        )
    )
    ws_repo.save(workspace)

    async def mock_transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": mock_reply}}]},
        )

    provider = OpenAiLlmProvider(
        api_key="mock-key",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        transport=httpx.MockTransport(mock_transport),
    )
    agent_repo = SqliteAgentRepository(sqlite_connection)
    agent_service = AgentService(
        agent_repo,
        provider,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )

    resource_repo = SqliteResourceRepository(sqlite_connection)
    receipt_repo = SqliteIdempotencyReceiptRepository(sqlite_connection)
    job_repo = SqliteIngestionJobRepository(sqlite_connection)
    resource_service = ResourceService(
        ws_repo, resource_repo, lambda: SqliteResearchUnitOfWork(sqlite_connection)
    )
    capture_service = CaptureService(
        ws_repo,
        job_repo,
        receipt_repo,
        resource_service,
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )
    orchestrator = CapturePlanningOrchestrator(
        capture_service=capture_service,
        work_planning_service=None,
        unit_of_work_factory=lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )

    client = FakeTelegramClient(allowed_chat_ids=frozenset({12345}))
    service = TelegramService(
        telegram_client=client,
        capture_planning_orchestrator=orchestrator,
        workspace_id=workspace.id,
        unit_of_work_factory=lambda: SqliteResearchUnitOfWork(sqlite_connection),
        agent_service=agent_service,
    )
    return service, client, agent_repo, workspace.id


def test_telegram_slash_agents_lists_available_agents(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, client, agent_repo, ws_id = _setup_service(sqlite_connection)
    update = TelegramUpdate(
        update_id=101,
        message=TelegramMessage(
            message_id=1,
            date=1720000000,
            chat=TelegramChat(id=12345),
            from_=TelegramUser(id=1, is_bot=False, username="alice"),
            text="/agents",
        ),
    )
    outcome = service.process_update(update)
    assert outcome == TelegramUpdateOutcome.AGENT_ANSWERED
    assert len(client.outbox) == 1
    chat_id, text = client.outbox[0]
    assert chat_id == 12345
    assert "Available Agents" in text
    assert "Research-Agent" in text


def test_telegram_mention_agent_routes_to_specific_agent(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, client, agent_repo, ws_id = _setup_service(
        sqlite_connection, mock_reply="Planned story breakdown."
    )
    plan_agent = next(a for a in agent_repo.list_agents() if a.name == "Plan-Agent")

    update = TelegramUpdate(
        update_id=102,
        message=TelegramMessage(
            message_id=2,
            date=1720000000,
            chat=TelegramChat(id=12345),
            from_=TelegramUser(id=1, is_bot=False, username="alice"),
            text="@Plan-Agent break down the authentication story",
        ),
    )
    outcome = service.process_update(update)
    assert outcome == TelegramUpdateOutcome.AGENT_ANSWERED
    assert len(client.outbox) == 1
    chat_id, text = client.outbox[0]
    assert "Planned story breakdown." in text
    assert "Plan-Agent" in text

    # Verify run recorded for Plan-Agent
    runs = agent_repo.list_runs_for_agent(plan_agent.id)
    assert len(runs) >= 1
    assert "Planned story breakdown." in runs[0].summary


def test_telegram_freeform_text_routes_to_default_agent(
    sqlite_connection: sqlite3.Connection,
) -> None:
    service, client, agent_repo, ws_id = _setup_service(
        sqlite_connection, mock_reply="Synthesis of graph knowledge."
    )
    research_agent = next(a for a in agent_repo.list_agents() if a.name == "Research-Agent")

    update = TelegramUpdate(
        update_id=103,
        message=TelegramMessage(
            message_id=3,
            date=1720000000,
            chat=TelegramChat(id=12345),
            from_=TelegramUser(id=1, is_bot=False, username="alice"),
            text="Tell me what research we have on transformers",
        ),
    )
    outcome = service.process_update(update)
    assert outcome == TelegramUpdateOutcome.AGENT_ANSWERED
    assert len(client.outbox) == 1
    chat_id, text = client.outbox[0]
    assert "Synthesis of graph knowledge." in text

    # Verify run recorded for Research-Agent
    runs = agent_repo.list_runs_for_agent(research_agent.id)
    assert len(runs) >= 1
