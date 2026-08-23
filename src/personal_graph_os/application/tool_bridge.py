"""Workspace tool layer for autonomous agent tool-calling loop (EP-2026-014 ST-T1 / ST-T2).

Maps model tool calls to clean domain services across tasks, wiki, research, graph, and activity.
Enforces per-agent allowlist policy and safe proposal write-mode mechanics.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from personal_graph_os.application.activity_service import ActivityService
from personal_graph_os.application.agent_adapters import AgentTool
from personal_graph_os.application.document_service import DocumentService
from personal_graph_os.application.repositories import AgentRepository, NodeRepository
from personal_graph_os.application.services import EdgeService, NodeService, ResourceService
from personal_graph_os.application.work_item_service import WorkItemService, WorkItemUpdatePatch
from personal_graph_os.domain.agents import Agent, AgentRun, AgentRunStatus, AgentWriteMode
from personal_graph_os.domain.documents import DocumentKind
from personal_graph_os.domain.identifiers import (
    CollectionId,
    DocumentId,
    EdgeTypeId,
    NodeId,
    ResourceId,
    WorkItemId,
    WorkspaceId,
)
from personal_graph_os.domain.work_items import (
    WorkItemKind,
    WorkItemPriority,
    WorkItemStatus,
    WorkItemType,
)

WORKSPACE_TOOLS: tuple[AgentTool, ...] = (
    AgentTool(
        name="tasks.list_tasks",
        description="List work items/tasks in the workspace with optional status and kind filters.",
        parameters={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": (
                        "Filter by status: backlog, planned, in_progress, in_review, "
                        "done, production, blocked, cancelled"
                    ),
                },
                "kind": {
                    "type": "string",
                    "description": "Filter by kind: epic, story, task",
                    "enum": ["epic", "story", "task"],
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of tasks to return (default 50)",
                },
            },
        },
    ),
    AgentTool(
        name="tasks.get",
        description="Get full details of a work item by ID.",
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The work item ID"},
            },
            "required": ["task_id"],
        },
    ),
    AgentTool(
        name="tasks.create",
        description="Create a new task, story, or epic in the workspace.",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Concise actionable title"},
                "kind": {
                    "type": "string",
                    "description": "Work item kind: task, story, epic",
                    "enum": ["task", "story", "epic"],
                    "default": "task",
                },
                "work_type": {
                    "type": "string",
                    "description": "Type of work: feature, fix, refactor, research, ops, docs",
                    "enum": ["feature", "fix", "refactor", "research", "ops", "docs"],
                    "default": "feature",
                },
                "status": {
                    "type": "string",
                    "description": "Initial status (default backlog)",
                    "default": "backlog",
                },
                "parent_id": {
                    "type": "string",
                    "description": "Optional parent work item ID (e.g. story ID for a task)",
                },
            },
            "required": ["title"],
        },
    ),
    AgentTool(
        name="tasks.set_status",
        description="Update the status of a work item.",
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "ID of the task to update"},
                "status": {
                    "type": "string",
                    "description": (
                        "New status: backlog, planned, in_progress, in_review, "
                        "done, production, blocked, cancelled"
                    ),
                    "enum": [
                        "backlog",
                        "planned",
                        "in_progress",
                        "in_review",
                        "done",
                        "production",
                        "blocked",
                        "cancelled",
                    ],
                },
            },
            "required": ["task_id", "status"],
        },
    ),
    AgentTool(
        name="tasks.update_fields",
        description=(
            "Update fields on a task (priority, assignee, due date, blockers, progress percent)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "ID of the task to update"},
                "priority": {
                    "type": "string",
                    "description": "Priority level (low, medium, high, critical)",
                    "enum": ["low", "medium", "high", "critical"],
                },
                "assignee": {"type": "string", "description": "Assignee name"},
                "due_date": {"type": "string", "description": "Due date in YYYY-MM-DD format"},
                "blockers": {"type": "string", "description": "Blocker notes"},
                "progress_percent": {
                    "type": "integer",
                    "description": "Progress percentage (0-100)",
                },
            },
            "required": ["task_id"],
        },
    ),
    AgentTool(
        name="tasks.delete",
        description="Delete / archive a task by ID.",
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "ID of the task to delete"},
            },
            "required": ["task_id"],
        },
    ),
    AgentTool(
        name="wiki.list",
        description="List wiki documents in the workspace.",
        parameters={
            "type": "object",
            "properties": {
                "collection_id": {
                    "type": "string",
                    "description": "Optional collection ID filter",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max documents to return (default 50)",
                },
            },
        },
    ),
    AgentTool(
        name="wiki.read",
        description="Read the complete content and metadata of a wiki document by ID.",
        parameters={
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "description": "Document ID"},
            },
            "required": ["document_id"],
        },
    ),
    AgentTool(
        name="wiki.create",
        description="Create a new wiki document in the workspace.",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Document title"},
                "body": {
                    "type": "string",
                    "description": "Markdown body content",
                    "default": "",
                },
                "kind": {
                    "type": "string",
                    "description": "Document kind (note, plan, lesson, documentation)",
                    "enum": ["note", "plan", "lesson", "documentation"],
                    "default": "note",
                },
                "collection_id": {"type": "string", "description": "Optional collection ID"},
            },
            "required": ["title"],
        },
    ),
    AgentTool(
        name="wiki.edit_body",
        description="Update the markdown body of an existing wiki document.",
        parameters={
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "description": "Document ID"},
                "body": {"type": "string", "description": "New markdown body content"},
            },
            "required": ["document_id", "body"],
        },
    ),
    AgentTool(
        name="wiki.add_tag",
        description="Add a tag to a wiki document.",
        parameters={
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "description": "Document ID"},
                "tag": {"type": "string", "description": "Tag label to attach"},
            },
            "required": ["document_id", "tag"],
        },
    ),
    AgentTool(
        name="research.list_papers",
        description="List research papers and resources with summary and takeaways.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Optional search term"},
                "limit": {"type": "integer", "description": "Max results (default 50)"},
            },
        },
    ),
    AgentTool(
        name="research.add_takeaway",
        description="Add a key takeaway to a research resource.",
        parameters={
            "type": "object",
            "properties": {
                "resource_id": {"type": "string", "description": "Resource ID"},
                "takeaway": {"type": "string", "description": "Key takeaway text to add"},
            },
            "required": ["resource_id", "takeaway"],
        },
    ),
    AgentTool(
        name="graph.list_nodes",
        description="List knowledge graph nodes in the workspace.",
        parameters={
            "type": "object",
            "properties": {
                "node_type": {
                    "type": "string",
                    "description": "Optional node type name/ID filter",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max nodes to return (default 50)",
                },
            },
        },
    ),
    AgentTool(
        name="graph.connect",
        description="Connect two nodes in the graph with a typed relation edge.",
        parameters={
            "type": "object",
            "properties": {
                "source_node_id": {"type": "string", "description": "Source node ID"},
                "target_node_id": {"type": "string", "description": "Target node ID"},
                "edge_type": {
                    "type": "string",
                    "description": "Relation type (e.g. relates_to, supports, implements, blocks)",
                    "default": "relates_to",
                },
            },
            "required": ["source_node_id", "target_node_id"],
        },
    ),
    AgentTool(
        name="activity.recent",
        description="List recent workspace activity audit events.",
        parameters={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max events to return (default 20)",
                },
            },
        },
    ),
)

_TOOL_MAP: dict[str, AgentTool] = {tool.name: tool for tool in WORKSPACE_TOOLS}


def to_openai_tool_schema(tool: AgentTool) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def to_anthropic_tool_schema(tool: AgentTool) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.parameters,
    }


@dataclass
class ToolExecutionResult:
    status: str
    output: Any
    is_proposal: bool = False
    run_id: str | None = None
    summary: str | None = None


class ToolBridge:
    """Dispatches tool calls to domain services, enforcing allowlists and proposal write modes."""

    def __init__(
        self,
        *,
        work_item_service: WorkItemService,
        document_service: DocumentService,
        resource_service: ResourceService,
        node_service: NodeService,
        edge_service: EdgeService,
        activity_service: ActivityService,
        agent_repository: AgentRepository,
        node_repository: NodeRepository | None = None,
        uow_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._work_item_service = work_item_service
        self._document_service = document_service
        self._resource_service = resource_service
        self._node_service = node_service
        self._edge_service = edge_service
        self._activity_service = activity_service
        self._agent_repository = agent_repository
        self._node_repository = node_repository
        self._uow_factory = uow_factory

    def _record_run(self, run: AgentRun) -> None:
        if self._uow_factory is not None:
            with self._uow_factory() as uow:
                uow.agents.record_run_without_commit(run)
        else:
            self._agent_repository.record_run_without_commit(run)

    def _get_node_title_body(self, node_id: NodeId) -> tuple[str, str]:
        if self._node_repository is not None:
            node = self._node_repository.get(node_id)
            if node:
                return node.title, node.body
        if self._uow_factory is not None:
            with self._uow_factory() as uow:
                node = uow.nodes.get(node_id)
                if node:
                    return node.title, node.body
        return "", ""

    def get_allowed_tools(self, allowlist: list[str] | None) -> list[AgentTool]:
        if not allowlist or "*" in allowlist:
            return list(WORKSPACE_TOOLS)
        aliases = {
            "search": "graph.list_nodes",
            "list_nodes": "graph.list_nodes",
            "read_node": "graph.list_nodes",
            "list_resources": "research.list_papers",
            "list_work_items": "tasks.list_tasks",
            "create_work_item": "tasks.create",
        }
        effective_allowlist = set(allowlist)
        for alias, target in aliases.items():
            if alias in effective_allowlist:
                effective_allowlist.add(target)

        return [tool for tool in WORKSPACE_TOOLS if tool.name in effective_allowlist]

    def get_openai_tools(self, allowlist: list[str] | None) -> list[dict[str, Any]]:
        return [to_openai_tool_schema(tool) for tool in self.get_allowed_tools(allowlist)]

    def get_anthropic_tools(self, allowlist: list[str] | None) -> list[dict[str, Any]]:
        return [to_anthropic_tool_schema(tool) for tool in self.get_allowed_tools(allowlist)]

    def is_tool_allowed(self, tool_name: str, agent: Agent) -> bool:
        allowed_names = {t.name for t in self.get_allowed_tools(agent.tool_allowlist)}
        return tool_name in allowed_names

    def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        agent: Agent,
        workspace_id: WorkspaceId,
    ) -> ToolExecutionResult:
        if not self.is_tool_allowed(tool_name, agent):
            return ToolExecutionResult(
                status="error",
                output={
                    "error": (
                        f"Tool '{tool_name}' is not in the allowlist for agent '{agent.name}'"
                    )
                },
            )

        is_proposal_mode = agent.write_mode in (AgentWriteMode.PROPOSAL, "proposal")

        try:
            # 1. Tasks tools
            if tool_name == "tasks.list_tasks":
                status_filter = arguments.get("status")
                kind_filter = arguments.get("kind")
                limit = int(arguments.get("limit", 50))
                items = self._work_item_service.list_workspace(workspace_id)
                results = []
                for item in items:
                    if status_filter and item.status.value != status_filter:
                        continue
                    if kind_filter and item.kind.value != kind_filter:
                        continue
                    title, _ = self._get_node_title_body(item.node_id)
                    results.append(
                        {
                            "id": str(item.id),
                            "title": title,
                            "status": item.status.value,
                            "kind": item.kind.value,
                            "work_type": item.work_type.value,
                            "priority": item.priority.value if item.priority else None,
                            "assignee": item.assignee,
                        }
                    )
                return ToolExecutionResult(status="ok", output=results[:limit])

            elif tool_name == "tasks.get":
                task_id = str(arguments["task_id"])
                item = self._work_item_service.get(WorkItemId(task_id))
                title, body = self._get_node_title_body(item.node_id)
                return ToolExecutionResult(
                    status="ok",
                    output={
                        "id": str(item.id),
                        "title": title,
                        "status": item.status.value,
                        "kind": item.kind.value,
                        "work_type": item.work_type.value,
                        "priority": item.priority.value if item.priority else None,
                        "due_date": item.due_date.isoformat() if item.due_date else None,
                        "assignee": item.assignee,
                        "blockers": item.blockers,
                        "progress_percent": item.progress_percent,
                        "body": body,
                    },
                )

            elif tool_name == "tasks.create":
                title = str(arguments["title"]).strip()
                kind_val = arguments.get("kind", "task")
                work_type_val = arguments.get("work_type", "feature")
                status_val = arguments.get("status", "backlog")
                parent_id_val = arguments.get("parent_id")

                kind = WorkItemKind(kind_val) if isinstance(kind_val, str) else kind_val
                work_type = (
                    WorkItemType(work_type_val)
                    if isinstance(work_type_val, str)
                    else work_type_val
                )
                status = WorkItemStatus(status_val) if isinstance(status_val, str) else status_val
                parent_id = WorkItemId(parent_id_val) if parent_id_val else None

                item, node = self._work_item_service.create(
                    workspace_id,
                    title=title,
                    kind=kind,
                    work_type=work_type,
                    status=status,
                    parent_id=parent_id,
                    source="agent",
                )
                summary = f"Created {item.kind.value} '{node.title}' ({item.id})"
                run = AgentRun.create(
                    agent_id=agent.id,
                    action="tasks.create",
                    summary=summary,
                    entity_type="task",
                    entity_id=str(item.id),
                    status=AgentRunStatus.APPLIED,
                    diff_json=json.dumps(
                        {"action": "create", "id": str(item.id), "title": node.title}
                    ),
                )
                self._record_run(run)

                return ToolExecutionResult(
                    status="ok",
                    output={
                        "id": str(item.id),
                        "title": node.title,
                        "status": item.status.value,
                        "kind": item.kind.value,
                        "work_type": item.work_type.value,
                        "message": f"Work item created successfully: {node.title}",
                    },
                    run_id=str(run.id),
                    summary=summary,
                )

            elif tool_name == "tasks.set_status":
                task_id = str(arguments["task_id"])
                new_status = str(arguments["status"])
                item = self._work_item_service.get(WorkItemId(task_id))
                title, _ = self._get_node_title_body(item.node_id)
                title = title or task_id

                if is_proposal_mode:
                    summary = f"Proposed status change for '{title}' to {new_status}"
                    diff_json = json.dumps(
                        {
                            "tool": "tasks.set_status",
                            "arguments": {"task_id": task_id, "status": new_status},
                            "prior_state": {"status": item.status.value, "title": title},
                        }
                    )
                    run = AgentRun.create(
                        agent_id=agent.id,
                        action="tasks.set_status",
                        summary=summary,
                        entity_type="task",
                        entity_id=task_id,
                        status=AgentRunStatus.PENDING_REVIEW,
                        diff_json=diff_json,
                    )
                    self._record_run(run)
                    return ToolExecutionResult(
                        status="pending_review",
                        output={
                            "status": "pending_review",
                            "run_id": str(run.id),
                            "task_id": task_id,
                            "proposed_status": new_status,
                            "summary": summary,
                            "message": (
                                "Status update proposed and waiting for approval in Agent Dock."
                            ),
                        },
                        is_proposal=True,
                        run_id=str(run.id),
                        summary=summary,
                    )
                else:
                    updated = self._work_item_service.update(
                        workspace_id,
                        WorkItemId(task_id),
                        WorkItemUpdatePatch(status=WorkItemStatus(new_status)),
                    )
                    summary = f"Changed status of '{title}' to {new_status}"
                    run = AgentRun.create(
                        agent_id=agent.id,
                        action="tasks.set_status",
                        summary=summary,
                        entity_type="task",
                        entity_id=task_id,
                        status=AgentRunStatus.APPLIED,
                        diff_json=json.dumps({"status": new_status}),
                    )
                    self._record_run(run)
                    return ToolExecutionResult(
                        status="ok",
                        output={
                            "id": str(updated.id),
                            "title": title,
                            "status": updated.status.value,
                        },
                        run_id=str(run.id),
                        summary=summary,
                    )

            elif tool_name == "tasks.update_fields":
                task_id = str(arguments["task_id"])
                item = self._work_item_service.get(WorkItemId(task_id))
                title, _ = self._get_node_title_body(item.node_id)
                title = title or task_id

                if is_proposal_mode:
                    summary = f"Proposed field updates for '{title}'"
                    diff_json = json.dumps(
                        {
                            "tool": "tasks.update_fields",
                            "arguments": arguments,
                            "prior_state": {
                                "priority": item.priority.value if item.priority else None,
                                "assignee": item.assignee,
                                "due_date": item.due_date.isoformat() if item.due_date else None,
                                "blockers": item.blockers,
                                "progress_percent": item.progress_percent,
                            },
                        }
                    )
                    run = AgentRun.create(
                        agent_id=agent.id,
                        action="tasks.update_fields",
                        summary=summary,
                        entity_type="task",
                        entity_id=task_id,
                        status=AgentRunStatus.PENDING_REVIEW,
                        diff_json=diff_json,
                    )
                    self._record_run(run)
                    return ToolExecutionResult(
                        status="pending_review",
                        output={
                            "status": "pending_review",
                            "run_id": str(run.id),
                            "summary": summary,
                            "message": (
                                "Field updates proposed and waiting for approval in Agent Dock."
                            ),
                        },
                        is_proposal=True,
                        run_id=str(run.id),
                        summary=summary,
                    )
                else:
                    due_date = (
                        date.fromisoformat(arguments["due_date"])
                        if arguments.get("due_date")
                        else None
                    )
                    priority = (
                        WorkItemPriority(arguments["priority"])
                        if arguments.get("priority")
                        else None
                    )
                    patch = WorkItemUpdatePatch(
                        priority=priority,
                        assignee=arguments.get("assignee"),
                        due_date=due_date,
                        blockers=arguments.get("blockers"),
                        progress_percent=arguments.get("progress_percent"),
                    )
                    updated = self._work_item_service.update(
                        workspace_id, WorkItemId(task_id), patch
                    )
                    summary = f"Updated fields on '{title}'"
                    run = AgentRun.create(
                        agent_id=agent.id,
                        action="tasks.update_fields",
                        summary=summary,
                        entity_type="task",
                        entity_id=task_id,
                        status=AgentRunStatus.APPLIED,
                        diff_json=json.dumps(arguments),
                    )
                    self._record_run(run)
                    return ToolExecutionResult(
                        status="ok", output={"id": str(updated.id), "title": title}
                    )

            elif tool_name == "tasks.delete":
                task_id = str(arguments["task_id"])
                item = self._work_item_service.get(WorkItemId(task_id))
                title, _ = self._get_node_title_body(item.node_id)
                title = title or task_id

                if is_proposal_mode:
                    summary = f"Proposed deletion of task '{title}' ({task_id})"
                    diff_json = json.dumps(
                        {
                            "tool": "tasks.delete",
                            "arguments": {"task_id": task_id},
                            "prior_state": {"title": title, "status": item.status.value},
                        }
                    )
                    run = AgentRun.create(
                        agent_id=agent.id,
                        action="tasks.delete",
                        summary=summary,
                        entity_type="task",
                        entity_id=task_id,
                        status=AgentRunStatus.PENDING_REVIEW,
                        diff_json=diff_json,
                    )
                    self._record_run(run)
                    return ToolExecutionResult(
                        status="pending_review",
                        output={
                            "status": "pending_review",
                            "run_id": str(run.id),
                            "task_id": task_id,
                            "task_title": title,
                            "summary": summary,
                            "message": (
                                f"Task deletion for '{title}' proposed and waiting for approval"
                                " in Agent Dock."
                            ),
                        },
                        is_proposal=True,
                        run_id=str(run.id),
                        summary=summary,
                    )
                else:
                    self._work_item_service.archive(workspace_id, WorkItemId(task_id))
                    summary = f"Deleted task '{title}' ({task_id})"
                    run = AgentRun.create(
                        agent_id=agent.id,
                        action="tasks.delete",
                        summary=summary,
                        entity_type="task",
                        entity_id=task_id,
                        status=AgentRunStatus.APPLIED,
                        diff_json=json.dumps({"action": "delete", "task_id": task_id}),
                    )
                    self._record_run(run)
                    return ToolExecutionResult(
                        status="ok",
                        output={"deleted": True, "task_id": task_id, "title": title},
                        run_id=str(run.id),
                        summary=summary,
                    )

            # 2. Wiki tools
            elif tool_name == "wiki.list":
                collection_id_val = arguments.get("collection_id")
                limit = int(arguments.get("limit", 50))
                collection_id = CollectionId(collection_id_val) if collection_id_val else None
                docs = self._document_service.list_documents(
                    workspace_id, collection_id=collection_id
                )
                results = [
                    {
                        "id": str(doc.id),
                        "title": doc.title,
                        "kind": doc.kind.value,
                        "collection_id": str(doc.collection_id) if doc.collection_id else None,
                    }
                    for doc in docs[:limit]
                ]
                return ToolExecutionResult(status="ok", output=results)

            elif tool_name == "wiki.read":
                doc_id = str(arguments["document_id"])
                detail = self._document_service.get_detail(DocumentId(doc_id))
                return ToolExecutionResult(
                    status="ok",
                    output={
                        "id": str(detail.document.id),
                        "title": detail.document.title,
                        "kind": detail.document.kind.value,
                        "body": detail.latest_version.body_markdown,
                        "tags": [t.name for t in detail.tags],
                        "version_count": detail.version_count,
                    },
                )

            elif tool_name == "wiki.create":
                title = str(arguments["title"]).strip()
                body = str(arguments.get("body", ""))
                kind_val = arguments.get("kind", "note")
                collection_id_val = arguments.get("collection_id")
                kind = DocumentKind(kind_val) if isinstance(kind_val, str) else kind_val
                collection_id = CollectionId(collection_id_val) if collection_id_val else None

                doc, _ = self._document_service.create_document(
                    workspace_id=workspace_id,
                    title=title,
                    kind=kind,
                    source="agent",
                    body_markdown=body,
                    collection_id=collection_id,
                )
                summary = f"Created wiki page '{doc.title}' ({doc.id})"
                run = AgentRun.create(
                    agent_id=agent.id,
                    action="wiki.create",
                    summary=summary,
                    entity_type="document",
                    entity_id=str(doc.id),
                    status=AgentRunStatus.APPLIED,
                    diff_json=json.dumps({"title": doc.title, "kind": doc.kind.value}),
                )
                self._record_run(run)
                return ToolExecutionResult(
                    status="ok",
                    output={"id": str(doc.id), "title": doc.title, "kind": doc.kind.value},
                    run_id=str(run.id),
                    summary=summary,
                )

            elif tool_name == "wiki.edit_body":
                doc_id = str(arguments["document_id"])
                new_body = str(arguments["body"])
                detail = self._document_service.get_detail(DocumentId(doc_id))

                if is_proposal_mode:
                    summary = f"Proposed body edit for wiki page '{detail.document.title}'"
                    diff_json = json.dumps(
                        {
                            "tool": "wiki.edit_body",
                            "arguments": {"document_id": doc_id, "body": new_body},
                            "prior_state": {
                                "body_length": len(detail.latest_version.body_markdown)
                            },
                        }
                    )
                    run = AgentRun.create(
                        agent_id=agent.id,
                        action="wiki.edit_body",
                        summary=summary,
                        entity_type="document",
                        entity_id=doc_id,
                        status=AgentRunStatus.PENDING_REVIEW,
                        diff_json=diff_json,
                    )
                    self._record_run(run)
                    return ToolExecutionResult(
                        status="pending_review",
                        output={
                            "status": "pending_review",
                            "run_id": str(run.id),
                            "document_id": doc_id,
                            "summary": summary,
                            "message": "Wiki edit proposed and waiting for approval in Agent Dock.",
                        },
                        is_proposal=True,
                        run_id=str(run.id),
                        summary=summary,
                    )
                else:
                    version = self._document_service.edit_body(
                        DocumentId(doc_id),
                        body_markdown=new_body,
                        actor="agent",
                    )
                    summary = (
                        f"Updated body of wiki page '{detail.document.title}' "
                        f"(v{version.version_number})"
                    )
                    run = AgentRun.create(
                        agent_id=agent.id,
                        action="wiki.edit_body",
                        summary=summary,
                        entity_type="document",
                        entity_id=doc_id,
                        status=AgentRunStatus.APPLIED,
                        diff_json=json.dumps({"version": version.version_number}),
                    )
                    self._record_run(run)
                    return ToolExecutionResult(
                        status="ok",
                        output={"id": doc_id, "version": version.version_number},
                        run_id=str(run.id),
                        summary=summary,
                    )

            elif tool_name == "wiki.add_tag":
                doc_id = str(arguments["document_id"])
                tag_label = str(arguments["tag"]).strip()
                tag = self._document_service.get_or_create_tag(workspace_id, tag_label)
                detail = self._document_service.get_detail(DocumentId(doc_id))
                current_tag_names = tuple(list(t.name for t in detail.tags) + [tag.name])
                self._document_service.update_metadata(
                    DocumentId(doc_id), tag_names=current_tag_names
                )
                return ToolExecutionResult(
                    status="ok",
                    output={"document_id": doc_id, "tag": tag.name},
                )

            # 3. Research tools
            elif tool_name == "research.list_papers":
                query = arguments.get("query")
                limit = int(arguments.get("limit", 50))
                resources = self._resource_service.list_by_workspace(workspace_id)
                results = []
                for r in resources:
                    title, _ = self._get_node_title_body(r.node_id)
                    if (
                        query
                        and query.lower() not in title.lower()
                        and (r.source_url and query.lower() not in r.source_url.lower())
                    ):
                        continue
                    results.append(
                        {
                            "id": str(r.id),
                            "title": title,
                            "kind": r.kind.value,
                            "url": r.source_url,
                            "lifecycle_status": r.lifecycle_status.value,
                            "takeaways_count": len(r.takeaways),
                            "takeaways": list(r.takeaways[:3]),
                        }
                    )
                return ToolExecutionResult(status="ok", output=results[:limit])

            elif tool_name == "research.add_takeaway":
                resource_id = str(arguments["resource_id"])
                takeaway = str(arguments["takeaway"]).strip()
                resource = self._resource_service.get(ResourceId(resource_id))
                title, _ = self._get_node_title_body(resource.node_id)
                updated_takeaways = tuple(list(resource.takeaways) + [takeaway])
                self._resource_service.update(
                    ResourceId(resource_id),
                    takeaways=updated_takeaways,
                )
                summary = f"Added takeaway to '{title}'"
                run = AgentRun.create(
                    agent_id=agent.id,
                    action="research.add_takeaway",
                    summary=summary,
                    entity_type="resource",
                    entity_id=resource_id,
                    status=AgentRunStatus.APPLIED,
                    diff_json=json.dumps({"takeaway": takeaway}),
                )
                self._record_run(run)
                return ToolExecutionResult(
                    status="ok",
                    output={"resource_id": resource_id, "takeaway": takeaway},
                    run_id=str(run.id),
                    summary=summary,
                )

            # 4. Graph tools
            elif tool_name == "graph.list_nodes":
                node_type = arguments.get("node_type")
                limit = int(arguments.get("limit", 50))
                if self._node_repository is not None:
                    nodes = self._node_repository.list_by_workspace(workspace_id)
                elif self._uow_factory is not None:
                    with self._uow_factory() as uow:
                        nodes = uow.nodes.list_by_workspace(workspace_id)
                else:
                    nodes = ()
                results = []
                for n in nodes:
                    if node_type and n.node_type_id != node_type:
                        continue
                    results.append(
                        {
                            "id": str(n.id),
                            "title": n.title,
                            "node_type_id": str(n.node_type_id),
                        }
                    )
                return ToolExecutionResult(status="ok", output=results[:limit])

            elif tool_name == "graph.connect":
                source_id = str(arguments["source_node_id"])
                target_id = str(arguments["target_node_id"])
                edge_type = str(arguments.get("edge_type", "relates_to"))

                edge = self._edge_service.connect(
                    workspace_id=workspace_id,
                    edge_type_id=EdgeTypeId(edge_type),
                    source_node_id=NodeId(source_id),
                    target_node_id=NodeId(target_id),
                )
                summary = f"Connected node {source_id} -> {target_id} ({edge_type})"
                run = AgentRun.create(
                    agent_id=agent.id,
                    action="graph.connect",
                    summary=summary,
                    entity_type="edge",
                    entity_id=str(edge.id),
                    status=AgentRunStatus.APPLIED,
                    diff_json=json.dumps(
                        {"source": source_id, "target": target_id, "type": edge_type}
                    ),
                )
                self._record_run(run)
                return ToolExecutionResult(
                    status="ok",
                    output={
                        "id": str(edge.id),
                        "source_node_id": source_id,
                        "target_node_id": target_id,
                        "edge_type": edge_type,
                    },
                    run_id=str(run.id),
                    summary=summary,
                )

            # 5. Activity tools
            elif tool_name == "activity.recent":
                limit = int(arguments.get("limit", 20))
                page = self._activity_service.list_workspace_events(workspace_id, limit=limit)
                events = page.events
                results = [
                    {
                        "id": str(e.id),
                        "action": e.action.value if hasattr(e.action, "value") else str(e.action),
                        "entity_type": e.entity_type,
                        "entity_id": e.entity_id,
                        "created_at": (
                            e.occurred_at.isoformat()
                            if hasattr(e.occurred_at, "isoformat")
                            else str(e.occurred_at)
                        ),
                    }
                    for e in events
                ]
                return ToolExecutionResult(status="ok", output=results)

            else:
                return ToolExecutionResult(
                    status="error",
                    output={"error": f"Unknown tool name: {tool_name}"},
                )

        except Exception as exc:
            return ToolExecutionResult(
                status="error",
                output={"error": f"{type(exc).__name__}: {exc}"},
            )

    def apply_proposal(self, run: AgentRun, workspace_id: WorkspaceId) -> dict[str, Any]:
        """Apply a pending_review proposal for real upon user approval."""
        if run.status != AgentRunStatus.PENDING_REVIEW:
            raise ValueError(
                f"Run {run.id} is not in pending_review status (current: {run.status})"
            )

        diff = json.loads(run.diff_json or "{}")
        tool_name = diff.get("tool") or run.action
        args = diff.get("arguments", {})

        if tool_name == "tasks.set_status":
            task_id = str(args["task_id"])
            new_status = str(args["status"])
            updated = self._work_item_service.update(
                workspace_id,
                WorkItemId(task_id),
                WorkItemUpdatePatch(status=WorkItemStatus(new_status)),
            )
            return {"applied": True, "task_id": str(updated.id), "status": updated.status.value}

        elif tool_name == "tasks.update_fields":
            task_id = str(args["task_id"])
            due_date = (
                date.fromisoformat(args["due_date"])
                if args.get("due_date")
                else None
            )
            priority = (
                WorkItemPriority(args["priority"])
                if args.get("priority")
                else None
            )
            patch = WorkItemUpdatePatch(
                priority=priority,
                assignee=args.get("assignee"),
                due_date=due_date,
                blockers=args.get("blockers"),
                progress_percent=args.get("progress_percent"),
            )
            updated = self._work_item_service.update(workspace_id, WorkItemId(task_id), patch)
            return {"applied": True, "task_id": str(updated.id)}

        elif tool_name == "tasks.delete":
            task_id = str(args["task_id"])
            self._work_item_service.archive(workspace_id, WorkItemId(task_id))
            return {"applied": True, "task_id": task_id, "deleted": True}

        elif tool_name == "wiki.edit_body":
            doc_id = str(args["document_id"])
            new_body = str(args["body"])
            version = self._document_service.edit_body(
                DocumentId(doc_id),
                body_markdown=new_body,
                actor="agent",
            )
            return {"applied": True, "document_id": doc_id, "version": version.version_number}

        else:
            raise ValueError(f"Unsupported proposal action for apply: {tool_name}")
