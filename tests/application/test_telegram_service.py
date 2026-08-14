from __future__ import annotations

import sqlite3

import pytest

from personal_graph_os.application.capture_planning_orchestrator import (
    CapturePlanningOrchestrator,
)
from personal_graph_os.application.capture_service import CaptureService
from personal_graph_os.application.enrichment_service import EnrichmentService
from personal_graph_os.application.semantic_schema import ensure_semantic_schema
from personal_graph_os.application.services import ResourceService, new_workspace
from personal_graph_os.application.telegram_adapters import (
    TelegramFetchFailedError,
    TelegramMessage,
    TelegramNotConfiguredError,
    TelegramUpdate,
    TelegramUser,
)
from personal_graph_os.application.telegram_service import (
    TELEGRAM_CHANNEL_NAME,
    TelegramService,
    TelegramUpdateOutcome,
)
from personal_graph_os.domain.extraction import (
    EvidenceKind,
    ExtractedContent,
    ExtractionEvidence,
    hash_content,
)
from personal_graph_os.domain.identifiers import ResourceId, WorkspaceId
from personal_graph_os.domain.resource import ResourceKind
from personal_graph_os.infrastructure.enrichment.fake_provider import FakeEnrichmentProvider
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteChannelSyncStateRepository,
    SqliteIdempotencyReceiptRepository,
    SqliteIngestionJobRepository,
    SqliteNodeRepository,
    SqliteResourceEnrichmentProfileRepository,
    SqliteResourceRepository,
    SqliteWorkspaceRepository,
)
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import (
    SqliteResearchUnitOfWork,
)
from personal_graph_os.infrastructure.telegram.fake_client import FakeTelegramClient


class _AckFailingClient(FakeTelegramClient):
    """Fault injection for S11-F03: `send_message` always fails."""

    def send_message(self, chat_id: int, text: str) -> None:
        raise TelegramFetchFailedError("ack send failed")


class _StubExtractionService:
    """Stands in for `ExtractionService` for S11-F05 telegram-level proof."""

    def extract(
        self, *, resource_kind: ResourceKind, canonical_identifier: str, source_url: str | None
    ) -> ExtractedContent:
        return ExtractedContent(
            resource_kind=resource_kind,
            canonical_identifier=canonical_identifier,
            title="A Test Article",
            abstract="An abstract for the captured article.",
            evidence=(
                ExtractionEvidence(
                    kind=EvidenceKind.METADATA_LOOKUP,
                    adapter_name="test-adapter",
                    source_reference=canonical_identifier,
                    content_hash=hash_content(f"evidence-for-{canonical_identifier}"),
                    byte_length=32,
                ),
            ),
        )


def _message(
    text: str, *, chat_id: int = 1, is_bot: bool = False, update_id: int = 1
) -> TelegramUpdate:
    return TelegramUpdate(
        update_id=update_id,
        message=TelegramMessage(
            message_id=update_id,
            date=1720000000,
            chat={"id": chat_id},
            from_=TelegramUser(id=99, is_bot=is_bot),
            text=text,
        ),
    )


def _service(
    sqlite_connection: sqlite3.Connection,
    *,
    updates: tuple[TelegramUpdate, ...] = (),
    allowed_chat_ids: frozenset[int] = frozenset({1}),
    client: object | None = ...,
    workspace_id: WorkspaceId | None = None,
    enrichment_service: object | None = None,
    extraction_service: object | None = None,
    agent_loop: object | None = None,
    pdf_text_extractor: object | None = None,
) -> tuple[TelegramService, FakeTelegramClient, WorkspaceId]:
    workspace_repository = SqliteWorkspaceRepository(sqlite_connection)
    if workspace_id is None:
        workspace = ensure_semantic_schema(new_workspace("Personal"))
        workspace_repository.save(workspace)
        resolved_workspace_id = workspace.id
    else:
        resolved_workspace_id = workspace_id
    unit_of_work_factory = lambda: SqliteResearchUnitOfWork(sqlite_connection)  # noqa: E731
    resource_service = ResourceService(
        workspace_repository, SqliteResourceRepository(sqlite_connection), unit_of_work_factory
    )
    capture_service = CaptureService(
        workspace_repository,
        SqliteIngestionJobRepository(sqlite_connection),
        SqliteIdempotencyReceiptRepository(sqlite_connection),
        resource_service,
        unit_of_work_factory,
    )
    orchestrator = CapturePlanningOrchestrator(
        capture_service,
        None,
        unit_of_work_factory,
        enrichment_service=enrichment_service,
        extraction_service=extraction_service,  # type: ignore[arg-type]
    )
    resolved_client = (
        FakeTelegramClient(updates, allowed_chat_ids=allowed_chat_ids) if client is ... else client
    )
    telegram_service = TelegramService(
        resolved_client,  # type: ignore[arg-type]
        orchestrator,
        resolved_workspace_id,
        unit_of_work_factory,
        agent_loop=agent_loop,  # type: ignore[arg-type]
        pdf_text_extractor=pdf_text_extractor,  # type: ignore[arg-type]
    )
    return telegram_service, resolved_client, resolved_workspace_id  # type: ignore[return-value]


def test_url_only_message_captures_a_raw_resource_and_advances_the_cursor(
    sqlite_connection: sqlite3.Connection,
) -> None:
    telegram_service, fake_client, workspace_id = _service(
        sqlite_connection, updates=(_message("https://example.com/article"),)
    )

    outcome = telegram_service.process_update(
        fake_client.get_updates(offset=None, timeout_seconds=25)[0]
    )

    assert outcome is TelegramUpdateOutcome.PROCESSED
    acks = [text for _chat_id, text in fake_client.outbox]
    assert any("Capturing https://example.com/article" in text for text in acks)
    assert any("Captured article:" in text for text in acks)

    cursor = SqliteChannelSyncStateRepository(sqlite_connection).get(
        workspace_id, TELEGRAM_CHANNEL_NAME
    )
    assert cursor is not None
    assert cursor.cursor_value == "00000000000000000001"


def test_explicit_operations_become_an_enrich_capture(
    sqlite_connection: sqlite3.Connection,
) -> None:
    telegram_service, fake_client, _workspace_id = _service(
        sqlite_connection,
        updates=(_message("summarize https://example.com/article"),),
    )

    outcome = telegram_service.process_update(
        fake_client.get_updates(offset=None, timeout_seconds=25)[0]
    )

    assert outcome is TelegramUpdateOutcome.PROCESSED
    acks = [text for _chat_id, text in fake_client.outbox]
    assert any("summarize" in text for text in acks)


def test_a_message_with_no_url_gets_a_clarification_ack_and_advances(
    sqlite_connection: sqlite3.Connection,
) -> None:
    telegram_service, fake_client, workspace_id = _service(
        sqlite_connection, updates=(_message("just a note"),)
    )

    outcome = telegram_service.process_update(
        fake_client.get_updates(offset=None, timeout_seconds=25)[0]
    )

    assert outcome is TelegramUpdateOutcome.SKIPPED_NO_URL
    assert any("could not find a link" in text for _chat_id, text in fake_client.outbox)
    cursor = SqliteChannelSyncStateRepository(sqlite_connection).get(
        workspace_id, TELEGRAM_CHANNEL_NAME
    )
    assert cursor is not None
    assert cursor.cursor_value == "00000000000000000001"


def test_unrecognized_segments_are_never_guessed(
    sqlite_connection: sqlite3.Connection,
) -> None:
    telegram_service, fake_client, _workspace_id = _service(
        sqlite_connection,
        updates=(_message("https://example.com/article do something clever"),),
    )

    outcome = telegram_service.process_update(
        fake_client.get_updates(offset=None, timeout_seconds=25)[0]
    )

    assert outcome is TelegramUpdateOutcome.CLARIFIED
    assert any("could not interpret" in text for _chat_id, text in fake_client.outbox)
    assert not any("Captured article" in text for _chat_id, text in fake_client.outbox)


def test_bot_messages_are_ignored_without_an_ack(
    sqlite_connection: sqlite3.Connection,
) -> None:
    telegram_service, fake_client, workspace_id = _service(
        sqlite_connection,
        updates=(_message("https://example.com/article", is_bot=True),),
    )

    outcome = telegram_service.process_update(
        fake_client.get_updates(offset=None, timeout_seconds=25)[0]
    )

    assert outcome is TelegramUpdateOutcome.SKIPPED_BOT
    assert fake_client.outbox == ()
    cursor = SqliteChannelSyncStateRepository(sqlite_connection).get(
        workspace_id, TELEGRAM_CHANNEL_NAME
    )
    assert cursor is not None


def test_disallowed_chats_are_ignored_without_an_ack(
    sqlite_connection: sqlite3.Connection,
) -> None:
    telegram_service, fake_client, _workspace_id = _service(
        sqlite_connection,
        updates=(_message("https://example.com/article", chat_id=99),),
        allowed_chat_ids=frozenset({1}),
    )

    outcome = telegram_service.process_update(
        fake_client.get_updates(offset=None, timeout_seconds=25)[0]
    )

    assert outcome is TelegramUpdateOutcome.SKIPPED_DISALLOWED
    assert fake_client.outbox == ()


def test_redelivering_the_same_update_replays_without_duplicating(
    sqlite_connection: sqlite3.Connection,
) -> None:
    update = _message("https://example.com/article")
    telegram_service, fake_client, workspace_id = _service(sqlite_connection, updates=(update,))

    first = telegram_service.process_update(update)
    second = telegram_service.process_update(update)

    assert first is TelegramUpdateOutcome.PROCESSED
    assert second is TelegramUpdateOutcome.PROCESSED
    acks = [text for _chat_id, text in fake_client.outbox]
    assert any("replay" in text for text in acks)

    jobs = SqliteIngestionJobRepository(sqlite_connection).list_by_workspace(workspace_id)
    assert len(jobs) == 1


def test_a_conflicted_redelivery_is_acked_and_advances(
    sqlite_connection: sqlite3.Connection,
) -> None:
    telegram_service, fake_client, workspace_id = _service(
        sqlite_connection, updates=(_message("https://example.com/article"),)
    )

    first = telegram_service.process_update(
        fake_client.get_updates(offset=None, timeout_seconds=25)[0]
    )
    fake_client._updates = (_message("https://other.example/thing", update_id=1),)
    second = telegram_service.process_update(
        fake_client.get_updates(offset=None, timeout_seconds=25)[0]
    )

    assert first is TelegramUpdateOutcome.PROCESSED
    assert second is TelegramUpdateOutcome.CONFLICTED
    cursor = SqliteChannelSyncStateRepository(sqlite_connection).get(
        workspace_id, TELEGRAM_CHANNEL_NAME
    )
    assert cursor is not None
    assert cursor.cursor_value == "00000000000000000001"


def test_the_cursor_survives_a_fresh_service_instance(
    sqlite_connection: sqlite3.Connection,
) -> None:
    telegram_service, fake_client, workspace_id = _service(
        sqlite_connection, updates=(_message("https://example.com/article"),)
    )
    telegram_service.process_update(fake_client.get_updates(offset=None, timeout_seconds=25)[0])

    restarted_service, _restart_client, _ws = _service(sqlite_connection, workspace_id=workspace_id)

    assert restarted_service.current_offset() == 2


def test_unconfigured_service_fails_closed(sqlite_connection: sqlite3.Connection) -> None:
    telegram_service, _client, _workspace_id = _service(sqlite_connection, client=None)

    with pytest.raises(TelegramNotConfiguredError):
        telegram_service.process_update(_message("https://example.com/article"))


def test_a_plan_command_is_acknowledged_and_left_pending_without_a_provider(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """A `plan <url>` message parses to a `plan` operation; with no planning provider configured
    it stays pending (ST-05 behavior), and the bot acknowledges the intent."""
    telegram_service, fake_client, workspace_id = _service(
        sqlite_connection, updates=(_message("plan https://example.com/article"),)
    )

    outcome = telegram_service.process_update(
        fake_client.get_updates(offset=None, timeout_seconds=25)[0]
    )

    assert outcome is TelegramUpdateOutcome.PROCESSED
    assert any("running: plan" in text for _chat_id, text in fake_client.outbox)
    job = SqliteIngestionJobRepository(sqlite_connection).get_by_source(
        workspace_id, "telegram", "1"
    )
    assert job is not None
    assert job.result_entity_type == "resource"


@pytest.mark.parametrize(("first", "second"), ((9, 10), (99, 100)))
def test_the_cursor_advances_across_a_digit_boundary(
    sqlite_connection: sqlite3.Connection, first: int, second: int
) -> None:
    """Regression for S11-F01: a lexicographic `MAX` on the padded cursor must agree with numeric
    order at every digit boundary, so `current_offset()` moves past it instead of re-delivering
    the boundary update forever."""
    first_url = f"https://example.com/{first}"
    second_url = f"https://example.com/{second}"
    fake_client = FakeTelegramClient(
        updates=(
            _message(first_url, update_id=first),
            _message(second_url, update_id=second),
        )
    )
    telegram_service, _client, workspace_id = _service(sqlite_connection)
    for update in fake_client.get_updates(offset=None, timeout_seconds=25):
        telegram_service.process_update(update)

    assert telegram_service.current_offset() == second + 1
    jobs = SqliteIngestionJobRepository(sqlite_connection).list_by_workspace(workspace_id)
    assert len(jobs) == 2


def _media_update(update_id: int = 3, *, caption: str | None = None) -> TelegramUpdate:
    return TelegramUpdate(
        update_id=update_id,
        message=TelegramMessage(
            message_id=update_id,
            date=1720000000,
            chat={"id": 1},
            from_=TelegramUser(id=99, is_bot=False),
            text=None,
            caption=caption,
        ),
    )


def test_a_media_message_without_a_caption_gets_the_link_ack(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """Regression for S11-F02: a media/attachment message is not silently dropped -- from an
    allowlisted chat it gets the planned 'send a URL' acknowledgement."""
    fake_client = FakeTelegramClient(updates=(_media_update(),))
    telegram_service, _client, _workspace_id = _service(sqlite_connection, client=fake_client)

    outcome = telegram_service.process_update(
        fake_client.get_updates(offset=None, timeout_seconds=25)[0]
    )

    assert outcome is TelegramUpdateOutcome.SKIPPED_NO_URL
    assert any("could not find a link" in text for _chat_id, text in fake_client.outbox)


def test_a_media_message_with_a_url_caption_is_captured(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """Regression for S11-F02/F06: a media caption carrying a URL is captured like a text
    message, and the caption is preserved as the captured resource body (S11-F06)."""
    fake_client = FakeTelegramClient(
        updates=(_media_update(caption="https://example.com/article"),)
    )
    telegram_service, _client, workspace_id = _service(sqlite_connection, client=fake_client)

    outcome = telegram_service.process_update(
        fake_client.get_updates(offset=None, timeout_seconds=25)[0]
    )

    assert outcome is TelegramUpdateOutcome.PROCESSED
    assert any("Captured article" in text for _chat_id, text in fake_client.outbox)
    job = SqliteIngestionJobRepository(sqlite_connection).get_by_source(
        workspace_id, "telegram", "3"
    )
    assert job is not None
    assert job.result_entity_type == "resource"
    resource = SqliteResourceRepository(sqlite_connection).get(
        ResourceId(job.result_entity_id or "")
    )
    assert resource is not None
    node = SqliteNodeRepository(sqlite_connection).get(resource.node_id)
    assert node is not None
    assert node.body == "https://example.com/article"


def test_a_summarize_message_runs_enrichment_and_persists_a_profile(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """Regression for S11-F05 at the channel level: a `summarize <url>` message is executed
    through the orchestrator, so the captured Article ends up with an enrichment profile --
    acceptance (2) is observably met, not just acknowledged."""
    enrichment_service = EnrichmentService(
        SqliteWorkspaceRepository(sqlite_connection),
        FakeEnrichmentProvider(),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )
    fake_client = FakeTelegramClient(updates=(_message("summarize https://example.com/article"),))
    telegram_service, _client, workspace_id = _service(
        sqlite_connection,
        client=fake_client,
        enrichment_service=enrichment_service,
        extraction_service=_StubExtractionService(),
    )

    outcome = telegram_service.process_update(
        fake_client.get_updates(offset=None, timeout_seconds=25)[0]
    )

    assert outcome is TelegramUpdateOutcome.PROCESSED
    job = SqliteIngestionJobRepository(sqlite_connection).get_by_source(
        workspace_id, "telegram", "1"
    )
    assert job is not None
    assert job.result_entity_type == "resource"
    resource = SqliteResourceRepository(sqlite_connection).get(
        ResourceId(job.result_entity_id or "")
    )
    assert resource is not None
    profile = SqliteResourceEnrichmentProfileRepository(sqlite_connection).get_by_identifier(
        workspace_id, resource.canonical_identifier
    )
    assert profile is not None
    assert profile.current_version_number == 1


def test_a_failed_clarification_ack_does_not_advance_the_cursor(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """Regression for S11-F03: the ack is sent before the cursor advances, so a transient ack
    failure leaves the update pending for the next poll instead of silently dropping it."""
    telegram_service, _client, workspace_id = _service(
        sqlite_connection, client=_AckFailingClient(updates=())
    )

    with pytest.raises(TelegramFetchFailedError):
        telegram_service.process_update(_message("just a note, no link"))

    cursor = SqliteChannelSyncStateRepository(sqlite_connection).get(
        workspace_id, TELEGRAM_CHANNEL_NAME
    )
    assert cursor is None


def test_a_redelivered_summarize_message_does_not_re_enrich(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """Regression for S11-F07 at the channel level: when a `summarize` update is redelivered
    (result ack failed before the cursor advanced), the capture replays but enrichment must not
    run again -- exactly one profile version, matching acceptance (3)."""
    enrichment_service = EnrichmentService(
        SqliteWorkspaceRepository(sqlite_connection),
        FakeEnrichmentProvider(),
        lambda: SqliteResearchUnitOfWork(sqlite_connection),
    )
    update = _message("summarize https://example.com/article")
    fake_client = FakeTelegramClient(updates=(update,))
    telegram_service, _client, workspace_id = _service(
        sqlite_connection,
        client=fake_client,
        enrichment_service=enrichment_service,
        extraction_service=_StubExtractionService(),
    )

    telegram_service.process_update(update)
    telegram_service.process_update(update)

    job = SqliteIngestionJobRepository(sqlite_connection).get_by_source(
        workspace_id, "telegram", "1"
    )
    assert job is not None
    resource = SqliteResourceRepository(sqlite_connection).get(
        ResourceId(job.result_entity_id or "")
    )
    assert resource is not None
    profile = SqliteResourceEnrichmentProfileRepository(sqlite_connection).get_by_identifier(
        workspace_id, resource.canonical_identifier
    )
    assert profile is not None
    assert profile.current_version_number == 1


class _FakeGraphAgent:
    """`GraphAgent` stub that records calls and returns a canned answer."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    @property
    def provider_name(self) -> str:
        return "stub"

    def run(
        self,
        *,
        user_message: str,
        actor_name: str,
        request_id: str,
        attachment_image_data_url: str | None = None,
    ) -> str:
        self.calls.append(
            {
                "user_message": user_message,
                "actor_name": actor_name,
                "request_id": request_id,
                "image": attachment_image_data_url,
            }
        )
        return "I analyzed it."


def _file_media_update(
    *,
    update_id: int = 21,
    photo_file_id: str | None = None,
    document: dict[str, object] | None = None,
) -> TelegramUpdate:
    return TelegramUpdate(
        update_id=update_id,
        message=TelegramMessage(
            message_id=update_id,
            date=1720000000,
            chat={"id": 1},
            from_=TelegramUser(id=99, username="alperen"),
            photo=[{"file_id": photo_file_id, "file_size": 10, "width": 2, "height": 2}]
            if photo_file_id
            else None,
            document=document,
        ),
    )


def test_image_attachment_is_analyzed_via_the_agent_loop(
    sqlite_connection: sqlite3.Connection,
) -> None:
    agent = _FakeGraphAgent()
    update = _file_media_update(photo_file_id="photo-1")
    client = FakeTelegramClient((update,), file_bytes={"photo-1": b"fake-image-bytes"})
    service, _client, _ws = _service(sqlite_connection, client=client, agent_loop=agent)

    outcome = service.process_update(update)

    assert outcome is TelegramUpdateOutcome.AGENT_ANSWERED
    assert agent.calls[0]["request_id"] == "21"
    assert agent.calls[0]["actor_name"] == "alperen"
    assert agent.calls[0]["image"] == "data:image/png;base64,ZmFrZS1pbWFnZS1ieXRlcw=="
    assert client.outbox == ((1, "I analyzed it."),)


def test_pdf_document_attachment_hands_extracted_text_to_the_agent(
    sqlite_connection: sqlite3.Connection,
) -> None:
    from tests.infrastructure.extraction.test_pdf_text import _text_pdf

    agent = _FakeGraphAgent()
    update = _file_media_update(
        document={
            "file_id": "doc-1",
            "file_name": "paper.pdf",
            "mime_type": "application/pdf",
        }
    )
    from personal_graph_os.infrastructure.extraction.pdf_text import extract_pdf_text

    client = FakeTelegramClient((update,), file_bytes={"doc-1": _text_pdf("Hello PDF World")})
    service, _client, _ws = _service(
        sqlite_connection, client=client, agent_loop=agent, pdf_text_extractor=extract_pdf_text
    )

    outcome = service.process_update(update)

    assert outcome is TelegramUpdateOutcome.AGENT_ANSWERED
    assert agent.calls[0]["request_id"] == "21"
    message = agent.calls[0]["user_message"]
    assert isinstance(message, str)
    assert "Hello PDF World" in message
    assert "--- DOCUMENT TEXT ---" in message
    assert client.outbox == ((1, "I analyzed it."),)
