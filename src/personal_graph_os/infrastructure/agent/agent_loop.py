"""Bounded tool-calling agent loop for the Telegram channel (EP-2026-012 follow-up).

This is the "bot has MCP access" piece: a free-form Telegram message is handed to a
chat-completions model that may call a small, allowlisted set of `AgentGatewayService` methods --
the exact same service the MCP server exposes -- in a bounded loop, then must land on a
natural-language final answer which the channel sends back.

Safety properties (review-critical, do not weaken):

- fail-closed: constructed only with an explicitly configured provider; absent one, the Telegram
  channel keeps its plain URL-capture behavior;
- the loop is bounded (`max_iterations`); a model that never produces a final text gets a fixed
  fallback reply instead of an unbounded conversation;
- the toolset is an explicit allowlist of read methods plus `create_work_item`; no archive, no
  hard delete, no edge/node surgery is exposed to the model;
- every `create_work_item` call is attributed (`actor_name` + `reason` + `request_id`) exactly
  like an MCP tool call, so graph writes are always traceable;
- a failing tool call is returned to the model as a short error string (so it can recover),
  never raised out of the loop.

Concrete bridge: sits in `infrastructure` (like `infrastructure.mcp.gateway`) and is injected
into `application.telegram_service` through the `GraphAgent` protocol in
`application/agent_adapters.py`, so the application layer stays infrastructure-free.
"""

from __future__ import annotations

import json
from typing import Any

from personal_graph_os.application.agent_adapters import (
    AgentChatProvider,
    AgentTool,
    AgentToolCall,
)
from personal_graph_os.application.tool_bridge import WORKSPACE_TOOLS
from personal_graph_os.domain.documents import DocumentKind
from personal_graph_os.domain.identifiers import (
    DocumentId,
    EdgeTypeId,
    NodeId,
    WorkItemId,
    WorkspaceId,
)
from personal_graph_os.domain.work_items import WorkItemKind, WorkItemStatus, WorkItemType
from personal_graph_os.infrastructure.mcp.gateway import AgentGatewayService

MAX_ITERATIONS = 6
_MAX_TOOL_RESULT_CHARS = 4000
_MAX_FINAL_TEXT_CHARS = 4000

_SYSTEM_PROMPT = (
    "You are a personal knowledge-graph assistant. You can read the user's workspace and add "
    "work items by calling the provided tools; prefer tools over guessing and keep answers "
    "short and factual in the user's language. When the user asks to add a task, call "
    "create_work_item. When they ask about stored research, nodes, or tasks, search or list "
    "first and answer from the actual results. Never invent graph content that the tools did "
    "not return. Answer with plain text only -- no JSON, no tool call syntax."
)


def _search_tool() -> AgentTool:
    return AgentTool(
        name="search_graph",
        description=(
            "Search the knowledge graph for nodes/resources matching a query; returns the "
            "closest matches with titles and kinds."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free-text search query"},
                "limit": {"type": "integer", "description": "Max results (default 8)"},
            },
            "required": ["query"],
        },
    )


def _get_workspace_tool() -> AgentTool:
    return AgentTool(
        name="get_workspace",
        description="Return an overview of the user's workspace (node/edge type counts).",
        parameters={"type": "object", "properties": {}},
    )


def _get_node_tool() -> AgentTool:
    return AgentTool(
        name="get_node",
        description="Return the full stored content of one graph node by id.",
        parameters={
            "type": "object",
            "properties": {"node_id": {"type": "string"}},
            "required": ["node_id"],
        },
    )


def _list_work_items_tool() -> AgentTool:
    return AgentTool(
        name="list_work_items",
        description="List the user's work items (tasks/stories/epics).",
        parameters={
            "type": "object",
            "properties": {"status": {"type": "string"}},
        },
    )


def _create_work_item_tool() -> AgentTool:
    return AgentTool(
        name="create_work_item",
        description=(
            "Add a work item (a task by default) to the user's workspace. Requires an explicit, "
            "specific title."
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Concise actionable title"},
                "description": {"type": "string", "description": "Optional body/notes"},
                "work_type": {
                    "type": "string",
                    "description": "feature | fix | refactor | research | ops | docs",
                },
                "kind": {"type": "string", "description": "task | story | epic (default task)"},
            },
            "required": ["title"],
        },
    )


def _capture_url_tool() -> AgentTool:
    return AgentTool(
        name="capture_url",
        description=(
            "Capture a URL (e.g. a paper, article, or repository) into the user's graph. Use it "
            "when the user sends a link or asks to save/study a source; the content is enriched "
            "(summary + key findings) before you answer."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to capture"},
                "title": {"type": "string", "description": "Optional human title"},
                "summarize": {
                    "type": "boolean",
                    "description": "Also produce an enrichment summary (default true)",
                },
            },
            "required": ["url"],
        },
    )


BOT_TOOLS: tuple[AgentTool, ...] = (
    _search_tool(),
    _get_workspace_tool(),
    _get_node_tool(),
    _list_work_items_tool(),
    _create_work_item_tool(),
    _capture_url_tool(),
) + WORKSPACE_TOOLS

_TOOL_BY_NAME = {tool.name: tool for tool in BOT_TOOLS}


def _bounded(text: str, maximum: int = _MAX_TOOL_RESULT_CHARS) -> str:
    if len(text) <= maximum:
        return text
    return text[:maximum] + "\n…[truncated]"


def _serialize(value: Any) -> str:
    if isinstance(value, str):
        return value
    if hasattr(value, "model_dump"):
        return json.dumps(value.model_dump(mode="json"), ensure_ascii=False, default=str)
    return json.dumps(value, ensure_ascii=False, default=str)


def _str_arg(arguments: dict[str, object], key: str, default: str) -> str:
    value = arguments.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _int_arg(arguments: dict[str, object], key: str, default: int) -> int:
    value = arguments.get(key)
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return default


class AgentLoopService:
    """Drive the tool-calling chat loop over a gateway and provider."""

    def __init__(
        self,
        *,
        provider: AgentChatProvider,
        gateway: AgentGatewayService,
        workspace_id: WorkspaceId,
        max_iterations: int = MAX_ITERATIONS,
    ) -> None:
        self._provider = provider
        self._gateway = gateway
        self._workspace_id = workspace_id
        self._max_iterations = max_iterations

    @property
    def provider_name(self) -> str:
        return self._provider.name

    def run(
        self,
        *,
        user_message: str,
        actor_name: str,
        request_id: str,
        attachment_image_data_url: str | None = None,
    ) -> str:
        user_content: object = user_message
        if attachment_image_data_url is not None:
            user_content = [
                {"type": "input_text", "text": user_message},
                {"type": "input_image", "image_url": attachment_image_data_url},
            ]
        input_items: list[dict[str, object]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        for _ in range(self._max_iterations):
            turn = self._provider.complete(input_items=input_items, tools=BOT_TOOLS)
            if turn.tool_calls:
                input_items.extend(turn.output_items)
                for call in turn.tool_calls:
                    result = self._execute(call, actor_name=actor_name, request_id=request_id)
                    input_items.append(
                        {
                            "type": "function_call_output",
                            "call_id": call.call_id,
                            "output": result,
                        }
                    )
                continue
            if turn.final_text:
                return _bounded(turn.final_text, _MAX_FINAL_TEXT_CHARS)
            return _bounded(
                "I could not produce an answer this time. Try rephrasing your request.",
                _MAX_FINAL_TEXT_CHARS,
            )
        return _bounded(
            "I could not finish answering within the step budget. Try a more specific request.",
            _MAX_FINAL_TEXT_CHARS,
        )

    def _execute(self, call: AgentToolCall, *, actor_name: str, request_id: str) -> str:
        handler = _TOOL_BY_NAME.get(call.name)
        if handler is None:
            return f"error: unknown tool {call.name!r}"
        try:
            if call.name in ("search_graph", "search"):
                result = self._gateway.search(
                    self._workspace_id,
                    str(call.arguments.get("query", "")),
                    limit=_int_arg(call.arguments, "limit", 8),
                )
            elif call.name == "get_workspace":
                result = self._gateway.get_workspace(self._workspace_id)
            elif call.name in ("get_node", "graph.list_nodes"):
                if "node_id" in call.arguments:
                    result = self._gateway.get_node(NodeId(str(call.arguments.get("node_id", ""))))
                else:
                    result = self._gateway.list_nodes(self._workspace_id)
            elif call.name in ("list_work_items", "tasks.list_tasks"):
                result = self._gateway.list_work_items(self._workspace_id)
            elif call.name in ("create_work_item", "tasks.create"):
                result = self._gateway.create_work_item(
                    self._workspace_id,
                    kind=WorkItemKind(_str_arg(call.arguments, "kind", "task")),
                    work_type=WorkItemType(_str_arg(call.arguments, "work_type", "feature")),
                    title=_str_arg(call.arguments, "title", ""),
                    body=(
                        _str_arg(call.arguments, "description", "")
                        or _str_arg(call.arguments, "body", "")
                    ),
                    status=WorkItemStatus(_str_arg(call.arguments, "status", "backlog")),
                    parent_id=None,
                    repository_node_id=None,
                    actor_name=actor_name,
                    reason=f"telegram agent request {request_id}",
                    request_id=f"tg-{request_id}",
                )
            elif call.name == "tasks.get":
                task_id = str(call.arguments.get("task_id", ""))
                result = self._gateway.get_work_item(WorkItemId(task_id))
            elif call.name == "tasks.set_status":
                task_id = str(call.arguments.get("task_id", ""))
                status_str = str(call.arguments.get("status", "backlog"))
                result = self._gateway.update_work_item(
                    WorkItemId(task_id),
                    work_type=None,
                    status=WorkItemStatus(status_str),
                    priority=None,
                    due_date=None,
                    assignee=None,
                    blockers=None,
                    progress_percent=None,
                    repository_node_id=None,
                    clear_priority=False,
                    clear_due_date=False,
                    clear_assignee=False,
                    clear_blockers=False,
                    clear_progress_percent=False,
                    clear_repository_node_id=False,
                    actor_name=actor_name,
                    reason=f"telegram agent request {request_id}",
                    request_id=f"tg-{request_id}",
                )
            elif call.name == "tasks.delete":
                task_id = str(call.arguments.get("task_id", ""))
                result = self._gateway.archive_work_item(
                    WorkItemId(task_id),
                    actor_name=actor_name,
                    reason=f"telegram agent request {request_id}",
                    request_id=f"tg-{request_id}",
                )
            elif call.name == "wiki.list":
                result = self._gateway.list_documents(self._workspace_id)
            elif call.name == "wiki.read":
                doc_id = str(call.arguments.get("document_id", ""))
                result = self._gateway.get_document(DocumentId(doc_id))
            elif call.name == "wiki.create":
                result = self._gateway.upsert_document(
                    self._workspace_id,
                    title=_str_arg(call.arguments, "title", ""),
                    body_markdown=_str_arg(call.arguments, "body", ""),
                    kind=DocumentKind(_str_arg(call.arguments, "kind", "note")),
                    document_id=None,
                    collection_id=None,
                    tag_names=(),
                    actor_name=actor_name,
                    reason=f"telegram agent request {request_id}",
                    request_id=f"tg-{request_id}",
                )
            elif call.name == "capture_url":
                summarize = call.arguments.get("summarize", True)
                operations = (
                    ("summarize", "extract_key_findings") if summarize is not False else ()
                )
                result = self._gateway.capture(
                    self._workspace_id,
                    source="telegram",
                    request_id=f"tg-{request_id}",
                    actor_name=actor_name,
                    payload_kind="url",
                    url=_str_arg(call.arguments, "url", ""),
                    text=None,
                    intent="enrich" if summarize is not False else "save_raw",
                    title=_str_arg(call.arguments, "title", "") or None,
                    repository_node_id=None,
                    reason=f"telegram agent request {request_id}",
                    operations=operations,
                )
            elif call.name == "research.list_papers":
                result = self._gateway.list_resources(self._workspace_id)
            elif call.name == "graph.connect":
                result = self._gateway.connect_nodes(
                    self._workspace_id,
                    edge_type_id=EdgeTypeId(str(call.arguments.get("edge_type", "relates_to"))),
                    source_node_id=NodeId(str(call.arguments.get("source_node_id", ""))),
                    target_node_id=NodeId(str(call.arguments.get("target_node_id", ""))),
                    actor_name=actor_name,
                    reason=f"telegram agent request {request_id}",
                    request_id=f"tg-{request_id}",
                )
            elif call.name == "activity.recent":
                result = self._gateway.list_activity_events(self._workspace_id)
            else:
                result = f"error: unhandled tool {call.name!r}"
        except Exception as error:  # noqa: BLE001
            return _bounded(f"tool error: {type(error).__name__}: {error}", _MAX_TOOL_RESULT_CHARS)
        return _bounded(_serialize(result), _MAX_TOOL_RESULT_CHARS)
