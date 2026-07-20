from datetime import date

import pytest

from app.schemas.ai_processing import DocumentAIExtraction
from app.services.calendar_event_validation import validate_temporal_events
from app.services.text_extraction import ExtractedTextPage


def _extraction(
    *,
    temporal_events: list[dict[str, object]],
    document_date: str | None = "2026-01-01",
) -> DocumentAIExtraction:
    return DocumentAIExtraction.model_validate(
        {
            "document_type": "invoice",
            "summary": "Invoice requiring payment.",
            "sender": "Example GmbH",
            "recipient": None,
            "document_date": document_date,
            "due_date": None,
            "total_amount": None,
            "currency": None,
            "invoice_number": None,
            "reference_number": None,
            "requires_action": True,
            "action_deadline": None,
            "confidence_score": 0.9,
            "notes": None,
            "temporal_events": temporal_events,
        }
    )


def _event(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "event_type": "payment_due",
        "title": "Pay invoice",
        "date": "2026-07-31",
        "datetime": None,
        "all_day": True,
        "timezone": None,
        "requires_action": True,
        "confidence_score": 0.95,
        "original_phrase": "2026-07-31",
        "source_field": "due_date",
        "reference_date": None,
        "evidence": {"quote": "Payment due: 2026-07-31", "page_number": 1},
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize(
    ("event", "text"),
    [
        (_event(), "Payment due: 2026-07-31"),
        (
            _event(
                date="2026-03-31",
                original_phrase="31. März 2026",
                evidence={"quote": "Zahlbar bis: 31. März 2026", "page_number": 1},
            ),
            "Zahlbar bis: 31. März 2026",
        ),
        (
            _event(
                original_phrase="July 31, 2026",
                evidence={"quote": "Payment due: July 31, 2026", "page_number": 1},
            ),
            "Payment due: July 31, 2026",
        ),
        (
            _event(
                original_phrase="31.07.2026",
                evidence={"quote": "Frist: 31.07.2026", "page_number": 1},
            ),
            "Frist: 31.07.2026",
        ),
    ],
)
def test_temporal_validation_accepts_supported_grounded_date_formats(
    event: dict[str, object],
    text: str,
) -> None:
    result = validate_temporal_events(
        extraction=_extraction(temporal_events=[event]),
        raw_text=text,
        source_pages=[ExtractedTextPage(page_number=1, text=text)],
    )

    candidate = result.events[0]
    assert candidate.status == "valid", str(candidate.model_dump())
    assert candidate.date_kind == "concrete"
    assert candidate.date_role == "event_date"
    assert candidate.resolved_date == date.fromisoformat(str(event["date"]))
    assert candidate.is_projectable is True


def test_temporal_validation_rejects_evidence_from_the_wrong_page() -> None:
    result = validate_temporal_events(
        extraction=_extraction(temporal_events=[_event()]),
        raw_text="Payment due: 2026-07-31",
        source_pages=[ExtractedTextPage(page_number=1, text="Different page text")],
    )

    candidate = result.events[0]
    assert candidate.status == "failed"
    assert candidate.issue_codes == ["evidence_quote_not_found"]
    assert candidate.is_projectable is False


def test_temporal_validation_detects_document_date_used_as_deadline() -> None:
    event = _event(
        date="2026-07-01",
        original_phrase="2026-07-01",
        evidence={"quote": "Invoice date: 2026-07-01", "page_number": 1},
    )
    result = validate_temporal_events(
        extraction=_extraction(temporal_events=[event]),
        raw_text="Invoice date: 2026-07-01",
    )

    candidate = result.events[0]
    assert candidate.status == "needs_review"
    assert candidate.date_role == "document_date"
    assert "document_date_used_as_event" in candidate.issue_codes
    assert candidate.is_projectable is False


def test_temporal_validation_keeps_unlabelled_date_as_projectable_warning() -> None:
    event = _event(
        original_phrase="July 31, 2026",
        evidence={"quote": "July 31, 2026", "page_number": 1},
    )
    result = validate_temporal_events(
        extraction=_extraction(temporal_events=[event]),
        raw_text="July 31, 2026",
    )

    candidate = result.events[0]
    assert candidate.status == "warning"
    assert candidate.date_role == "unknown"
    assert candidate.issue_codes == ["event_date_context_unclear"]
    assert candidate.is_projectable is True


def test_temporal_validation_marks_unresolved_relative_deadline_for_review() -> None:
    event = _event(
        date=None,
        datetime=None,
        original_phrase="within 14 days",
        evidence={"quote": "Payment due within 14 days", "page_number": 1},
    )
    result = validate_temporal_events(
        extraction=_extraction(temporal_events=[event]),
        raw_text="Payment due within 14 days",
    )

    candidate = result.events[0]
    assert candidate.status == "needs_review"
    assert candidate.date_kind == "relative"
    assert candidate.issue_codes == ["relative_date_unresolved"]


def test_temporal_validation_accepts_relative_deadline_with_grounded_reference() -> None:
    event = _event(
        date="2026-07-15",
        original_phrase="within 14 days",
        reference_date="2026-07-01",
        evidence={
            "quote": "Payment due within 14 days from invoice date 2026-07-01",
            "page_number": 1,
        },
    )
    result = validate_temporal_events(
        extraction=_extraction(temporal_events=[event]),
        raw_text="Payment due within 14 days from invoice date 2026-07-01",
    )

    candidate = result.events[0]
    assert candidate.status == "valid"
    assert candidate.date_kind == "relative"
    assert candidate.resolved_date == date(2026, 7, 15)


def test_temporal_validation_rejects_template_placeholders() -> None:
    event = _event(
        date=None,
        original_phrase="tt.mm.jjjj",
        evidence={"quote": "Payment due: tt.mm.jjjj", "page_number": 1},
    )
    source = """
        MUSTERBRIEF
        So verwenden Sie diesen Musterbrief.
        Payment due: tt.mm.jjjj
    """
    result = validate_temporal_events(
        extraction=_extraction(temporal_events=[event]),
        raw_text=source,
    )

    candidate = result.events[0]
    assert candidate.status == "failed"
    assert candidate.date_kind == "template_placeholder"
    assert "template_placeholder" in candidate.issue_codes
    assert candidate.is_projectable is False


def test_temporal_validation_rejects_deadline_before_document_date() -> None:
    event = _event(
        date="2026-06-30",
        original_phrase="2026-06-30",
        evidence={"quote": "Payment due: 2026-06-30", "page_number": 1},
    )
    result = validate_temporal_events(
        extraction=_extraction(
            temporal_events=[event],
            document_date="2026-07-01",
        ),
        raw_text="Payment due: 2026-06-30",
    )

    candidate = result.events[0]
    assert candidate.status == "failed"
    assert "event_before_document_date" in candidate.issue_codes


def test_temporal_validation_detects_conflicting_candidates_for_one_source_field() -> None:
    first = _event()
    second = _event(
        title="Pay corrected invoice",
        date="2026-08-15",
        original_phrase="2026-08-15",
        evidence={"quote": "Payment due: 2026-08-15", "page_number": 1},
    )
    source = "Payment due: 2026-07-31\nPayment due: 2026-08-15"
    result = validate_temporal_events(
        extraction=_extraction(temporal_events=[first, second]),
        raw_text=source,
    )

    assert [candidate.status for candidate in result.events] == [
        "needs_review",
        "needs_review",
    ]
    assert all(
        "conflicting_event_dates" in candidate.issue_codes
        and candidate.is_projectable is False
        for candidate in result.events
    )
