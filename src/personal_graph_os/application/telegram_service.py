"""Telegram channel ingress (EP-2026-012 ST-11).

`TelegramService` turns one inbound Telegram `TelegramUpdate` into the same `CaptureEnvelope`
pipeline every other channel uses, then acknowledges the result back into the chat. It is the
per-message unit the poller drives:

- eligibility is enforced here (skip non-message/bot/disallowed-chat updates, advancing the
  cursor so Telegram confirms them without re-processing);
- the message is parsed with the ST-02 vocabulary (`parse_capture_text`): a URL-only message is
  `save_raw`; comma-separated recognized operations make it `enrich`; a message with no URL, or
  with unrecognized segments, gets a clarification ack -- never a guess;
- `request_id == update_id` + `source="telegram"` reuse ST-02 idempotent replay, so a Telegram
  redelivery (cursor not yet advanced because an ack failed) replays instead of duplicating;
- the cursor only advances after the update is handled, so a failed update stays pending for the
  next poll.

Only ack messages are ever sent back to Telegram; the bot performs no other write.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from personal_graph_os.application.agent_adapters import GraphAgent
from personal_graph_os.application.capture_planning_orchestrator import (
    CapturePlanningOrchestrator,
)
from personal_graph_os.application.capture_service import CaptureOutcome
from personal_graph_os.application.pdf_adapters import PdfTextExtractor
from personal_graph_os.application.research_unit_of_work import ResearchUnitOfWork
from personal_graph_os.application.telegram_adapters import (
    TelegramClient,
    TelegramError,
    TelegramMessage,
    TelegramNotConfiguredError,
    TelegramUpdate,
)
from personal_graph_os.application.work_planning_service import WorkPlanOutcome
from personal_graph_os.domain.capture import (
    CaptureEnvelope,
    CaptureIdempotencyConflictError,
    CaptureIntent,
    CaptureOperationKind,
    CapturePayloadKind,
)
from personal_graph_os.domain.capture_parsing import ParsedCaptureText, parse_capture_text
from personal_graph_os.domain.channel_sync import ChannelSyncState
from personal_graph_os.domain.identifiers import DocumentId, ResourceId, WorkspaceId

TELEGRAM_CHANNEL_NAME = "telegram"
TELEGRAM_SOURCE_NAME = "telegram"
TELEGRAM_ACTOR_NAME = "telegram"
# The shared `channel_sync_state` cursor is lexicographically monotonic (`MAX`), so each channel
# must write a fixed-width, lexicographically-sortable string. Telegram's `update_id` is a
# variable-width integer ("9" then "10" would lexicographically regress), so it is zero-padded to
# a fixed width that numeric ordering and lexicographic ordering agree on (S11-F01).
TELEGRAM_CURSOR_WIDTH = 20

_OPERATION_LABELS = {
    CaptureOperationKind.SUMMARIZE: "summarize",
    CaptureOperationKind.EXTRACT_KEY_FINDINGS: "extract key findings",
    CaptureOperationKind.RELATE_TO: "relate to",
    CaptureOperationKind.PLAN: "plan",
}


class TelegramUpdateOutcome(StrEnum):
    """What handling one Telegram update resolved to (drives poller accounting/tests)."""

    PROCESSED = "processed"
    CONFLICTED = "conflicted"
    CLARIFIED = "clarified"
    AGENT_ANSWERED = "agent_answered"
    SKIPPED_NO_MESSAGE = "skipped_no_message"
    SKIPPED_BOT = "skipped_bot"
    SKIPPED_DISALLOWED = "skipped_disallowed"
    SKIPPED_NO_URL = "skipped_no_url"


class TelegramService:
    def __init__(
        self,
        telegram_client: TelegramClient | None,
        capture_planning_orchestrator: CapturePlanningOrchestrator,
        workspace_id: WorkspaceId,
        unit_of_work_factory: Callable[[], ResearchUnitOfWork],
        agent_loop: GraphAgent | None = None,
        pdf_text_extractor: PdfTextExtractor | None = None,
    ) -> None:
        self._telegram_client = telegram_client
        self._capture_planning_orchestrator = capture_planning_orchestrator
        self._workspace_id = workspace_id
        self._unit_of_work_factory = unit_of_work_factory
        self._agent_loop = agent_loop
        self._pdf_text_extractor = pdf_text_extractor

    def current_offset(self) -> int | None:
        """The next `getUpdates` offset: one past the last confirmed `update_id`, or `None` on a
        fresh start (Telegram's long-poll then serves the whole backlog)."""
        with self._unit_of_work_factory() as unit_of_work:
            state = unit_of_work.channel_sync_state.get(self._workspace_id, TELEGRAM_CHANNEL_NAME)
        if state is None:
            return None
        return int(state.cursor_value) + 1

    def process_update(self, update: TelegramUpdate) -> TelegramUpdateOutcome:
        client = self._telegram_client
        if client is None:
            raise TelegramNotConfiguredError(
                "Telegram is not configured: set PGOS_TELEGRAM_ENABLED, a token, and an "
                "allowlist to enable it"
            )
        message = update.message
        if message is None:
            self._advance_cursor(update.update_id)
            return TelegramUpdateOutcome.SKIPPED_NO_MESSAGE
        if message.from_ is not None and message.from_.is_bot:
            self._advance_cursor(update.update_id)
            return TelegramUpdateOutcome.SKIPPED_BOT
        if not client.is_chat_allowed(message.chat.id):
            self._advance_cursor(update.update_id)
            return TelegramUpdateOutcome.SKIPPED_DISALLOWED

        if message.media_file is not None and self._agent_loop is not None:
            return self._answer_media_via_agent(client, message, update.update_id)

        content = message.content
        if content is None:
            # A media/attachment message with no caption: ack the planned "send a URL" reply so
            # it is never silently dropped (S11-F02), then advance.
            client.send_message(
                message.chat.id, "I could not find a link in your message. Send a URL."
            )
            self._advance_cursor(update.update_id)
            return TelegramUpdateOutcome.SKIPPED_NO_URL

        parsed = parse_capture_text(content)
        if parsed.detected_url is None:
            if self._agent_loop is not None:
                return self._answer_via_agent(client, message, content, update.update_id)
            # Ack first, then advance (S11-F03): a failed ack must leave the update pending so
            # the next poll retries it instead of silently dropping the clarification.
            client.send_message(
                message.chat.id, "I could not find a link in your message. Send a URL."
            )
            self._advance_cursor(update.update_id)
            return TelegramUpdateOutcome.SKIPPED_NO_URL
        if parsed.unrecognized_segments:
            # A URL plus free-form intent (e.g. "save this and summarize it"): with an agent
            # configured the natural-language intent is handled by the agent (it can capture the
            # URL and answer); without one the bounded vocabulary clarification stays.
            if self._agent_loop is not None:
                return self._answer_via_agent(client, message, content, update.update_id)
            client.send_message(
                message.chat.id,
                f"I could not interpret: {'; '.join(parsed.unrecognized_segments)}. "
                "Send only a URL, or a URL followed by recognized commands such as "
                "'summarize', 'extract key findings', 'relate to <name>', or 'plan'.",
            )
            self._advance_cursor(update.update_id)
            return TelegramUpdateOutcome.CLARIFIED

        self._acknowledge_intent(client, message.chat.id, parsed)
        envelope = self._build_envelope(update, parsed)
        try:
            outcome, plan_outcome = self._capture_planning_orchestrator.submit(envelope)
        except CaptureIdempotencyConflictError as error:
            # The update was genuinely delivered and rejected; re-delivering it would conflict
            # identically, so confirm it and tell the user rather than retrying forever.
            client.send_message(message.chat.id, str(error))
            self._advance_cursor(update.update_id)
            return TelegramUpdateOutcome.CONFLICTED
        client.send_message(message.chat.id, self._result_text(outcome, plan_outcome))
        self._advance_cursor(update.update_id)
        return TelegramUpdateOutcome.PROCESSED

    def _answer_via_agent(
        self,
        client: TelegramClient,
        message: TelegramMessage,
        content: str,
        update_id: int,
    ) -> TelegramUpdateOutcome:
        """Free-form (non-URL) message with an agent configured: run the bounded graph agent
        and reply with its final text. A failed agent run must leave the update pending so the
        next poll retries it instead of silently dropping the user's question."""
        loop = self._agent_loop
        if loop is None:  # pragma: no cover - guarded by caller
            raise AssertionError("agent loop must be configured")
        actor_name = (
            message.from_.username
            if message.from_ and message.from_.username
            else TELEGRAM_ACTOR_NAME
        )
        answer = loop.run(
            user_message=content,
            actor_name=actor_name,
            request_id=str(update_id),
        )
        client.send_message(message.chat.id, answer)
        self._advance_cursor(update_id)
        return TelegramUpdateOutcome.AGENT_ANSWERED

    def _answer_media_via_agent(
        self,
        client: TelegramClient,
        message: TelegramMessage,
        update_id: int,
    ) -> TelegramUpdateOutcome:
        """A photo/document attachment with an agent configured: download the file, hand it to
        the bounded graph agent (vision for images, extracted PDF text for documents), and reply
        with its analysis. A failed download/analysis must leave the update pending so the next
        poll retries it instead of silently dropping the attachment."""
        loop = self._agent_loop
        if loop is None:  # pragma: no cover - guarded by caller
            raise AssertionError("agent loop must be configured")
        media = message.media_file
        if media is None:  # pragma: no cover - guarded by caller
            raise AssertionError("media message must carry a file")
        kind, file_id, file_name = media
        content = message.content
        text = content or (
            "Analyze this image and describe what it shows in detail."
            if kind == "image"
            else "Analyze this document and summarize its contents in detail."
        )
        actor_name = (
            message.from_.username
            if message.from_ and message.from_.username
            else TELEGRAM_ACTOR_NAME
        )
        try:
            file_bytes = client.download_file(file_id)
        except TelegramError:
            client.send_message(
                message.chat.id,
                "I could not download that file. Please try again or send a URL.",
            )
            self._advance_cursor(update_id)
            return TelegramUpdateOutcome.AGENT_ANSWERED

        if kind == "image":
            import base64

            data_url = f"data:image/png;base64,{base64.b64encode(file_bytes).decode('ascii')}"
            answer = loop.run(
                user_message=text,
                actor_name=actor_name,
                request_id=str(update_id),
                attachment_image_data_url=data_url,
            )
        else:
            if self._pdf_text_extractor is None:
                client.send_message(
                    message.chat.id,
                    "I could not read that document: PDF extraction is not configured on this "
                    "instance. Send a URL or paste the text instead.",
                )
                self._advance_cursor(update_id)
                return TelegramUpdateOutcome.AGENT_ANSWERED
            pdf_text = self._pdf_text_extractor(file_bytes)
            if not pdf_text:
                client.send_message(
                    message.chat.id,
                    "I could not read any text from that document. Please send a readable "
                    "PDF or a URL.",
                )
                self._advance_cursor(update_id)
                return TelegramUpdateOutcome.AGENT_ANSWERED
            answer = loop.run(
                user_message=f"{text}\n\n--- DOCUMENT TEXT ---\n{pdf_text}",
                actor_name=actor_name,
                request_id=str(update_id),
            )
        client.send_message(message.chat.id, answer)
        self._advance_cursor(update_id)
        return TelegramUpdateOutcome.AGENT_ANSWERED

    def _acknowledge_intent(
        self, client: TelegramClient, chat_id: int, parsed: ParsedCaptureText
    ) -> None:
        url = parsed.detected_url or ""
        if not parsed.operations:
            client.send_message(chat_id, f"Capturing {url} ...")
            return
        labels = [_OPERATION_LABELS[operation.kind] for operation in parsed.operations]
        client.send_message(chat_id, f"Capturing {url} and running: {', '.join(labels)} ...")

    def _build_envelope(self, update: TelegramUpdate, parsed: ParsedCaptureText) -> CaptureEnvelope:
        intent = CaptureIntent.ENRICH if parsed.operations else CaptureIntent.SAVE_RAW
        message = update.message
        content = message.content if message is not None else None
        return CaptureEnvelope(
            workspace_id=self._workspace_id,
            source=TELEGRAM_SOURCE_NAME,
            request_id=str(update.update_id),
            actor_name=TELEGRAM_ACTOR_NAME,
            payload_kind=CapturePayloadKind.URL,
            url=parsed.detected_url,
            # `content` (text or media caption) so a caption-only message keeps its caption as
            # the captured body/evidence (S11-F06).
            text=content,
            intent=intent,
            operations=parsed.operations,
        )

    def _result_text(self, outcome: CaptureOutcome, plan_outcome: WorkPlanOutcome | None) -> str:
        parts: list[str] = []
        if outcome.resource_id is not None:
            title = self._resource_title(outcome.resource_id)
            parts.append(f"Captured article: {title} ({outcome.resource_id})")
        elif outcome.document_id is not None:
            title = self._document_title(outcome.document_id)
            parts.append(f"Captured note: {title} ({outcome.document_id})")
        if plan_outcome is not None:
            parts.append(
                f"Planned as Epic {plan_outcome.epic.id} "
                f"(plan document {plan_outcome.plan_document.id})"
            )
        if outcome.was_replayed:
            parts.append("replay")
        return " | ".join(parts)

    def _resource_title(self, resource_id: str) -> str:
        with self._unit_of_work_factory() as unit_of_work:
            resource = unit_of_work.resources.get(ResourceId(resource_id))
            if resource is None:
                return resource_id
            node = unit_of_work.nodes.get(resource.node_id)
        if node is None:
            return resource_id
        return node.title

    def _document_title(self, document_id: str) -> str:
        with self._unit_of_work_factory() as unit_of_work:
            document = unit_of_work.documents.get(DocumentId(document_id))
        if document is None:
            return document_id
        return document.title

    def _advance_cursor(self, update_id: int) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.channel_sync_state.save_without_commit(
                ChannelSyncState(
                    workspace_id=self._workspace_id,
                    channel=TELEGRAM_CHANNEL_NAME,
                    cursor_value=str(update_id).zfill(TELEGRAM_CURSOR_WIDTH),
                )
            )
