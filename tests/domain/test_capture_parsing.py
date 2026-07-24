from __future__ import annotations

from personal_graph_os.domain.capture import CaptureOperationKind
from personal_graph_os.domain.capture_parsing import parse_capture_text


def test_parses_a_bare_url_with_no_operations() -> None:
    parsed = parse_capture_text("https://arxiv.org/abs/2401.00001")

    assert parsed.detected_url == "https://arxiv.org/abs/2401.00001"
    assert parsed.operations == ()
    assert not parsed.is_ambiguous


def test_parses_the_documented_telegram_style_example() -> None:
    parsed = parse_capture_text(
        "https://github.com/octocat/hello-world summarize, extract key findings, "
        "relate to apilex-agent"
    )

    assert parsed.detected_url == "https://github.com/octocat/hello-world"
    assert [operation.kind for operation in parsed.operations] == [
        CaptureOperationKind.SUMMARIZE,
        CaptureOperationKind.EXTRACT_KEY_FINDINGS,
        CaptureOperationKind.RELATE_TO,
    ]
    relate_operation = parsed.operations[-1]
    assert relate_operation.target == "apilex-agent"
    assert not parsed.is_ambiguous


def test_plan_this_phrase_is_recognized() -> None:
    parsed = parse_capture_text("https://example.com/spec plan this")

    assert [operation.kind for operation in parsed.operations] == [CaptureOperationKind.PLAN]


def test_unrecognized_segment_is_reported_and_not_dropped() -> None:
    parsed = parse_capture_text("https://example.com/article do something clever with this")

    assert parsed.detected_url == "https://example.com/article"
    assert parsed.operations == ()
    assert parsed.unrecognized_segments == ("do something clever with this",)
    assert parsed.is_ambiguous


def test_text_with_no_url_at_all_has_no_detected_url() -> None:
    parsed = parse_capture_text("just a note to self, nothing to fetch")

    assert parsed.detected_url is None
    assert parsed.unrecognized_segments == ("just a note to self", "nothing to fetch")


def test_relate_to_without_a_target_is_unrecognized_not_silently_dropped() -> None:
    parsed = parse_capture_text("https://example.com relate to")

    assert parsed.operations == ()
    assert parsed.unrecognized_segments == ("relate to",)
