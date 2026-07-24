"""Bounded, deterministic, offline parsing of explicit capture message text.

EP-2026-012 ST-02 decision: an incoming channel message such as
`"<url> summarize, extract key findings, relate to <repository>"` is parsed against a fixed,
small vocabulary -- never an LLM call, never a guess. A comma-separated segment that does not
match one of the recognized phrases below is reported back as unrecognized rather than silently
dropped or interpreted loosely; the caller (`CaptureService`) turns that into a clarification
request instead of acting on an assumption. Agentic classification of arbitrary free text is
ST-04's job, not this one.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from personal_graph_os.domain.capture import CaptureOperation, CaptureOperationKind

_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
_TRAILING_PUNCTUATION = ").,;:!?"

_SUMMARIZE_PHRASES = frozenset({"summarize", "summarise"})
_KEY_FINDINGS_PHRASES = frozenset({"extract key findings", "key findings"})
_PLAN_PHRASES = frozenset({"plan this", "plan it", "plan"})
_RELATE_TO_PREFIX = "relate to"


class ParsedCaptureText(BaseModel):
    """The result of parsing one free-text capture message."""

    detected_url: str | None
    operations: tuple[CaptureOperation, ...]
    unrecognized_segments: tuple[str, ...]

    @property
    def is_ambiguous(self) -> bool:
        return len(self.unrecognized_segments) > 0


def _extract_url(text: str) -> tuple[str | None, str]:
    match = _URL_PATTERN.search(text)
    if match is None:
        return None, text
    url = match.group(0).rstrip(_TRAILING_PUNCTUATION)
    remainder = text[: match.start()] + text[match.start() + len(url) :]
    return url, remainder


def _parse_segment(segment: str) -> CaptureOperation | None:
    lowered = segment.lower()
    if lowered.startswith(_RELATE_TO_PREFIX):
        target = segment[len(_RELATE_TO_PREFIX) :].strip(" :")
        if not target:
            return None
        return CaptureOperation(kind=CaptureOperationKind.RELATE_TO, target=target)
    if lowered in _SUMMARIZE_PHRASES:
        return CaptureOperation(kind=CaptureOperationKind.SUMMARIZE)
    if lowered in _KEY_FINDINGS_PHRASES:
        return CaptureOperation(kind=CaptureOperationKind.EXTRACT_KEY_FINDINGS)
    if lowered in _PLAN_PHRASES:
        return CaptureOperation(kind=CaptureOperationKind.PLAN)
    return None


def parse_capture_text(text: str) -> ParsedCaptureText:
    """Extract an optional URL plus a bounded operations list from one free-text message.

    A comma-separated segment left over after removing the URL is either a recognized operation
    phrase or an unrecognized segment -- there is no partial/fuzzy match.
    """
    detected_url, remainder = _extract_url(text)
    operations: list[CaptureOperation] = []
    unrecognized: list[str] = []
    for raw_segment in remainder.split(","):
        segment = raw_segment.strip()
        if not segment:
            continue
        operation = _parse_segment(segment)
        if operation is not None:
            operations.append(operation)
        else:
            unrecognized.append(segment)
    return ParsedCaptureText(
        detected_url=detected_url,
        operations=tuple(operations),
        unrecognized_segments=tuple(unrecognized),
    )
