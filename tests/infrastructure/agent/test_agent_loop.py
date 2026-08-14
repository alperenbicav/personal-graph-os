"""Unit tests for the bounded Telegram agent loop (fake provider + stub gateway)."""

from __future__ import annotations

from collections.abc import Sequence

from personal_graph_os.application.agent_adapters import AgentChatTurn, AgentToolCall
from personal_graph_os.infrastructure.agent.agent_loop import (
    MAX_ITERATIONS,
    AgentLoopService,
)


class ScriptedProvider:
    def __init__(self, turns: Sequence[AgentChatTurn]) -> None:
        self._turns = list(turns)
        self.calls: list[list[dict[str, object]]] = []

    @property
    def name(self) -> str:
        return "scripted"

    def complete(
        self, *, input_items: list[dict[str, object]], tools: tuple[object, ...] = ()
    ) -> AgentChatTurn:
        self.calls.append(input_items)
        return self._turns.pop(0)


class StubGateway:
    def __init__(self) -> None:
        self.searches: list[tuple[str, str, int]] = []
        self.created: list[dict[str, object]] = []
        self.captures: list[dict[str, object]] = []

    def search(self, workspace_id, query_text, *, include_archived=False, scope="all", limit=8):
        self.searches.append((workspace_id, query_text, limit))
        return {"hits": [{"title": "repo A", "kind": "github_repository"}]}

    def get_workspace(self, workspace_id):
        return _Json({"id": workspace_id, "name": "Personal", "node_type_count": 9})

    def get_node(self, node_id):
        return _Json({"id": node_id, "title": "Node"})

    def list_work_items(self, workspace_id, *, include_archived=False):
        return (_Json({"id": "w1", "title": "Existing task"}),)

    def capture(
        self,
        workspace_id,
        *,
        source,
        request_id,
        actor_name,
        payload_kind,
        url,
        text,
        intent,
        title,
        repository_node_id,
        reason,
        operations=(),
    ):
        self.captures.append(
            {
                "workspace_id": workspace_id,
                "source": source,
                "request_id": request_id,
                "actor_name": actor_name,
                "url": url,
                "intent": intent,
                "title": title,
                "operations": list(operations),
            }
        )
        return {
            "ingestion_job_id": "job-1",
            "resource_id": "res-1",
            "needs_clarification": False,
            "was_replayed": False,
        }

    def create_work_item(
        self,
        workspace_id,
        *,
        kind,
        work_type,
        title,
        body,
        status,
        parent_id,
        repository_node_id,
        actor_name,
        reason,
        request_id,
    ):
        self.created.append(
            {
                "kind": kind.value,
                "work_type": work_type.value,
                "title": title,
                "status": status.value,
                "actor_name": actor_name,
                "request_id": request_id,
            }
        )
        return _Json({"id": "w9", "title": title, "status": status.value})


class _Json:
    def __init__(self, value: dict[str, object]) -> None:
        self._value = value

    def model_dump(self, *, mode: str = "json") -> dict[str, object]:
        return self._value


def _turn_with_call(
    name: str, arguments: dict[str, object], *, call_id: str = "call_1"
) -> AgentChatTurn:
    return AgentChatTurn(
        final_text=None,
        tool_calls=(AgentToolCall(call_id=call_id, name=name, arguments=arguments),),
        output_items=(
            {
                "type": "function_call",
                "id": f"fc-{call_id}",
                "call_id": call_id,
                "name": name,
                "arguments": "{}",
            },
        ),
    )


def _make_loop(provider: ScriptedProvider, gateway: StubGateway) -> AgentLoopService:
    return AgentLoopService(provider=provider, gateway=gateway, workspace_id="ws-1")  # type: ignore[arg-type]


def test_answers_directly_without_tools() -> None:
    provider = ScriptedProvider([AgentChatTurn(final_text="3 papers stored.")])
    loop = _make_loop(provider, StubGateway())

    answer = loop.run(user_message="what's stored?", actor_name="tg", request_id="1")

    assert answer == "3 papers stored."


def test_search_then_final_answer() -> None:
    gateway = StubGateway()
    provider = ScriptedProvider(
        [
            _turn_with_call("search_graph", {"query": "repo"}),
            AgentChatTurn(final_text="Found repo A."),
        ]
    )
    loop = _make_loop(provider, gateway)

    answer = loop.run(user_message="find repos", actor_name="tg", request_id="1")

    assert answer == "Found repo A."
    assert gateway.searches == [("ws-1", "repo", 8)]
    second_input = provider.calls[1]
    assert any(
        item.get("type") == "function_call_output" and "repo A" in str(item.get("output"))
        for item in second_input
    )


def test_create_work_item_is_attributed() -> None:
    gateway = StubGateway()
    provider = ScriptedProvider(
        [
            _turn_with_call(
                "create_work_item",
                {"title": "Write docs", "work_type": "docs", "description": "Document the API"},
            ),
            AgentChatTurn(final_text="Added the task."),
        ]
    )
    loop = _make_loop(provider, gateway)

    answer = loop.run(
        user_message="add a task to write docs", actor_name="alperen", request_id="42"
    )

    assert answer == "Added the task."
    assert gateway.created == [
        {
            "kind": "task",
            "work_type": "docs",
            "title": "Write docs",
            "status": "backlog",
            "actor_name": "alperen",
            "request_id": "tg-42",
        }
    ]


def test_tool_error_is_fed_back_and_model_recovers() -> None:
    gateway = StubGateway()

    def boom(*args, **kwargs):
        raise ValueError("node not found")

    gateway.get_node = boom
    provider = ScriptedProvider(
        [
            _turn_with_call("get_node", {"node_id": "nope"}),
            AgentChatTurn(final_text="I could not find that node."),
        ]
    )
    loop = _make_loop(provider, gateway)

    answer = loop.run(user_message="get node", actor_name="tg", request_id="1")

    assert answer == "I could not find that node."
    second_input = provider.calls[1]
    assert any(
        item.get("type") == "function_call_output" and "node not found" in str(item.get("output"))
        for item in second_input
    )


def test_unknown_tool_is_rejected_without_calling_gateway() -> None:
    gateway = StubGateway()
    provider = ScriptedProvider(
        [
            _turn_with_call("delete_everything", {}),
            AgentChatTurn(final_text="I do not have that capability."),
        ]
    )
    loop = _make_loop(provider, gateway)

    answer = loop.run(user_message="delete all", actor_name="tg", request_id="1")

    assert answer == "I do not have that capability."
    second_input = provider.calls[1]
    assert any(
        item.get("type") == "function_call_output" and "unknown tool" in str(item.get("output"))
        for item in second_input
    )


def test_capture_url_calls_gateway_with_enrichment_operations() -> None:
    gateway = StubGateway()
    provider = ScriptedProvider(
        [
            _turn_with_call(
                "capture_url",
                {"url": "https://arxiv.org/abs/2607.18261", "title": "A paper"},
            ),
            AgentChatTurn(final_text="Captured and summarized the paper."),
        ]
    )
    loop = _make_loop(provider, gateway)

    answer = loop.run(
        user_message="save this paper and summarize it https://arxiv.org/abs/2607.18261",
        actor_name="alperen",
        request_id="7",
    )

    assert answer == "Captured and summarized the paper."
    assert gateway.captures == [
        {
            "workspace_id": "ws-1",
            "source": "telegram",
            "request_id": "tg-7",
            "actor_name": "alperen",
            "url": "https://arxiv.org/abs/2607.18261",
            "intent": "enrich",
            "title": "A paper",
            "operations": ["summarize", "extract_key_findings"],
        }
    ]


def test_image_attachment_is_sent_as_a_vision_input_part() -> None:
    provider = ScriptedProvider([AgentChatTurn(final_text="I see a signup form.")])
    loop = _make_loop(provider, StubGateway())

    answer = loop.run(
        user_message="What is on this screenshot?",
        actor_name="tg",
        request_id="1",
        attachment_image_data_url="data:image/png;base64,AAAA",
    )

    assert answer == "I see a signup form."
    user_item = provider.calls[0][1]
    parts = user_item["content"]
    assert isinstance(parts, list)
    assert parts[0] == {"type": "input_text", "text": "What is on this screenshot?"}
    assert parts[1]["type"] == "input_image"
    assert parts[1]["image_url"] == "data:image/png;base64,AAAA"


def test_loop_budget_is_bounded() -> None:
    gateway = StubGateway()
    provider = ScriptedProvider(
        [_turn_with_call("search_graph", {"query": "x"})] * (MAX_ITERATIONS + 2)
    )
    loop = _make_loop(provider, gateway)

    answer = loop.run(user_message="keep searching", actor_name="tg", request_id="1")

    assert "step budget" in answer
    assert len(provider.calls) == MAX_ITERATIONS
