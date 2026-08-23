"""Agent routes (EP-2026-014 ST-02): agent registry CRUD and chat message streaming."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from personal_graph_os.api.dependencies import get_agent_service
from personal_graph_os.api.schemas import (
    AgentMessageRequest,
    AgentResponse,
    AgentRunResponse,
    AgentSystemStatusResponse,
    CreateAgentRequest,
    UpdateAgentRequest,
)
from personal_graph_os.application.agent_service import AgentService
from personal_graph_os.application.llm_provider import LlmNotConfiguredError
from personal_graph_os.domain.identifiers import AgentId, AgentRunId

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=list[AgentResponse])
def list_agents(agents: AgentService = Depends(get_agent_service)) -> list[AgentResponse]:
    return [AgentResponse.from_domain(agent) for agent in agents.list_agents()]


@router.get("/status", response_model=AgentSystemStatusResponse)
def get_agents_status(
    agents: AgentService = Depends(get_agent_service),
) -> AgentSystemStatusResponse:
    all_agents = agents.list_agents()
    is_configured = agents.llm_provider is not None
    provider_name = os.environ.get("PGOS_LLM_PROVIDER", "openai").lower()
    model_name = os.environ.get("PGOS_LLM_MODEL", "default")
    unconfigured_reason = None
    if not is_configured:
        unconfigured_reason = (
            "LLM provider not configured. Set PGOS_LLM_API_KEY (and optionally "
            "PGOS_LLM_PROVIDER, PGOS_LLM_MODEL) environment variables."
        )
    return AgentSystemStatusResponse(
        configured=is_configured,
        provider=provider_name,
        model=model_name,
        available_agents_count=len(all_agents),
        unconfigured_reason=unconfigured_reason,
    )


@router.get("/runs", response_model=list[AgentRunResponse])
def list_all_agent_runs(
    limit: int = Query(default=50, ge=1, le=200),
    agents: AgentService = Depends(get_agent_service),
) -> list[AgentRunResponse]:
    runs = agents.list_recent_runs(limit=limit)
    return [AgentRunResponse.from_domain(run) for run in runs]


@router.post("/runs/{run_id}/approve", response_model=AgentRunResponse)
def approve_agent_run(
    run_id: str,
    agents: AgentService = Depends(get_agent_service),
) -> AgentRunResponse:
    run = agents.approve_run(AgentRunId(run_id))
    return AgentRunResponse.from_domain(run)


@router.post("/runs/{run_id}/reject", response_model=AgentRunResponse)
def reject_agent_run(
    run_id: str,
    agents: AgentService = Depends(get_agent_service),
) -> AgentRunResponse:
    run = agents.reject_run(AgentRunId(run_id))
    return AgentRunResponse.from_domain(run)


@router.post("", response_model=AgentResponse, status_code=201)
def create_agent(
    payload: CreateAgentRequest,
    agents: AgentService = Depends(get_agent_service),
) -> AgentResponse:
    agent = agents.create_agent(
        name=payload.name,
        system_prompt=payload.system_prompt,
        emoji=payload.emoji,
        tool_allowlist=payload.tool_allowlist,
        model=payload.model,
        write_mode=payload.write_mode,
    )
    return AgentResponse.from_domain(agent)


@router.get("/{agent_id}", response_model=AgentResponse)
def get_agent(
    agent_id: str,
    agents: AgentService = Depends(get_agent_service),
) -> AgentResponse:
    agent = agents.get_agent(AgentId(agent_id))
    return AgentResponse.from_domain(agent)


@router.patch("/{agent_id}", response_model=AgentResponse)
def update_agent(
    agent_id: str,
    payload: UpdateAgentRequest,
    agents: AgentService = Depends(get_agent_service),
) -> AgentResponse:
    agent = agents.update_agent(
        AgentId(agent_id),
        name=payload.name,
        emoji=payload.emoji,
        system_prompt=payload.system_prompt,
        tool_allowlist=payload.tool_allowlist,
        model=payload.model,
        write_mode=payload.write_mode,
    )
    return AgentResponse.from_domain(agent)


@router.delete("/{agent_id}", status_code=204)
def delete_agent(
    agent_id: str,
    agents: AgentService = Depends(get_agent_service),
) -> None:
    agents.delete_agent(AgentId(agent_id))


@router.get("/{agent_id}/runs", response_model=list[AgentRunResponse])
def list_agent_runs(
    agent_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    agents: AgentService = Depends(get_agent_service),
) -> list[AgentRunResponse]:
    runs = agents.list_runs(AgentId(agent_id), limit=limit)
    return [AgentRunResponse.from_domain(run) for run in runs]


@router.post("/{agent_id}/message")
async def send_agent_message(
    agent_id: str,
    payload: AgentMessageRequest,
    request: Request,
    stream: bool | None = Query(default=None),
    agents: AgentService = Depends(get_agent_service),
) -> Response:
    if agents.llm_provider is None:
        raise LlmNotConfiguredError("LLM provider not configured")

    agent = agents.get_agent(AgentId(agent_id))

    accept_header = request.headers.get("accept", "")
    is_json_fallback = (
        stream is False
        or ("application/json" in accept_header and "text/event-stream" not in accept_header)
    )

    if is_json_fallback:
        reply = await agents.complete_message(agent, payload.content)
        run = agents.record_run(
            agent_id=agent.id,
            action="message",
            summary=reply[:200] if reply else payload.content[:200],
        )
        return JSONResponse(content={"reply": reply, "run_id": str(run.id)})

    async def event_stream() -> AsyncIterator[str]:
        chunks: list[str] = []
        try:
            async for chunk in agents.stream_message(agent, payload.content):
                chunks.append(chunk)
                yield f"data: {json.dumps({'text': chunk})}\n\n"
            full_reply = "".join(chunks)
            run = agents.record_run(
                agent_id=agent.id,
                action="message",
                summary=full_reply[:200] if full_reply else payload.content[:200],
            )
            yield f"data: {json.dumps({'run_id': str(run.id), 'done': True})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
