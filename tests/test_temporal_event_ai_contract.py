from datetime import date, datetime
from types import SimpleNamespace

import pytest
from openai.lib._pydantic import to_strict_json_schema
from pydantic import ValidationError

import app.services.ai_processing as ai_processing
import app.tasks.documents as document_tasks
from app.schemas.ai_processing import (
    MAX_TEMPORAL_EVENTS_PER_DOCUMENT,
    DocumentAIExtraction,
    TemporalEventExtraction,
)
from app.services.ai_processing import OpenAIUsage, StandardAIProcessingResult
from app.services.calendar_event_validation import validate_temporal_events
from app.services.extraction_validation import ExtractionValidationResult
from app.services.text_extraction import ExtractedTextPage


def _document_extraction_data(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "document_type": "invoice",
        "summary": "Invoice requiring payment.",
        "sender": "Example GmbH",
        "recipient": None,
        "document_date": "2026-07-01",
        "due_date": "2026-07-31",
        "total_amount": 100.0,
        "currency": "EUR",
        "invoice_number": "INV-123",
        "reference_number": None,
        "requires_action": True,
        "action_deadline": "2026-07-31",
        "confidence_score": 0.95,
        "notes": None,
    }
    data.update(overrides)
    return data


def _event_data(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "event_type": "payment_due",
        "title": "Pay invoice INV-123",
        "date": "2026-07-31",
        "datetime": None,
        "all_day": True,
        "timezone": None,
        "requires_action": True,
        "confidence_score": 0.96,
        "original_phrase": "bis zum 31.07.2026",
        "source_field": "due_date",
        "reference_date": None,
        "evidence": {
            "quote": "Bitte zahlen Sie bis zum 31.07.2026.",
            "page_number": 2,
        },
    }
    data.update(overrides)
    return data


def test_temporal_events_are_backward_compatible_with_existing_contract() -> None:
    extraction = DocumentAIExtraction.model_validate(_document_extraction_data())

    assert extraction.due_date == "2026-07-31"
    assert extraction.action_deadline == "2026-07-31"
    assert extraction.temporal_events == []


def test_temporal_contract_is_valid_openai_strict_json_schema() -> None:
    schema = to_strict_json_schema(DocumentAIExtraction)
    event_schema = schema["$defs"]["TemporalEventExtraction"]

    assert event_schema["additionalProperties"] is False
    assert set(event_schema["required"]) == set(event_schema["properties"])
    assert event_schema["properties"]["event_type"]["enum"] == [
        "payment_due",
        "response_deadline",
        "action_deadline",
        "appointment",
        "contract_start",
        "contract_end",
        "cancellation_deadline",
        "renewal",
    ]


def test_temporal_event_contains_bounded_grounded_contract() -> None:
    extraction = DocumentAIExtraction.model_validate(
        _document_extraction_data(temporal_events=[_event_data()])
    )

    event = extraction.temporal_events[0]
    assert event.event_type == "payment_due"
    assert event.title == "Pay invoice INV-123"
    assert event.date == date(2026, 7, 31)
    assert event.datetime is None
    assert event.all_day is True
    assert event.requires_action is True
    assert event.confidence_score == 0.96
    assert event.original_phrase == "bis zum 31.07.2026"
    assert event.source_field == "due_date"
    assert event.evidence.quote == "Bitte zahlen Sie bis zum 31.07.2026."
    assert event.evidence.page_number == 2


def test_timed_temporal_event_accepts_explicit_timezone() -> None:
    event = TemporalEventExtraction.model_validate(
        _event_data(
            event_type="appointment",
            date=None,
            datetime="2026-08-03T09:30:00+02:00",
            all_day=False,
            timezone="Europe/Berlin",
            requires_action=False,
            original_phrase="3 August 2026 at 09:30 Europe/Berlin",
            source_field="appointment_date",
        )
    )

    assert event.datetime == datetime.fromisoformat("2026-08-03T09:30:00+02:00")
    assert event.timezone == "Europe/Berlin"


def test_timed_temporal_event_does_not_require_invented_timezone() -> None:
    event = TemporalEventExtraction.model_validate(
        _event_data(
            event_type="appointment",
            date=None,
            datetime="2026-08-03T09:30:00",
            all_day=False,
            timezone=None,
            requires_action=False,
            original_phrase="3 August 2026 at 09:30",
            source_field="appointment_date",
        )
    )

    assert event.datetime == datetime(2026, 8, 3, 9, 30)
    assert event.timezone is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_type", "other_deadline"),
        ("source_field", "free_form_date"),
    ],
)
def test_temporal_event_rejects_unknown_contract_values(
    field: str,
    value: str,
) -> None:
    with pytest.raises(ValidationError):
        TemporalEventExtraction.model_validate(_event_data(**{field: value}))


def test_temporal_events_are_limited_per_document() -> None:
    events = [_event_data(title=f"Event {index}") for index in range(21)]

    with pytest.raises(ValidationError) as exc_info:
        DocumentAIExtraction.model_validate(
            _document_extraction_data(temporal_events=events)
        )

    assert MAX_TEMPORAL_EVENTS_PER_DOCUMENT == 20
    assert exc_info.value.errors()[0]["type"] == "too_long"


@pytest.mark.parametrize(
    "overrides",
    [
        {"datetime": "2026-07-31T12:00:00+02:00"},
        {
            "date": "2026-07-31",
            "datetime": "2026-07-31T12:00:00+02:00",
            "all_day": False,
        },
        {"timezone": "Berlin local time"},
    ],
)
def test_temporal_event_rejects_invalid_time_representations(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        TemporalEventExtraction.model_validate(_event_data(**overrides))


def test_ambiguous_temporal_date_is_preserved_without_guessing() -> None:
    event = TemporalEventExtraction.model_validate(
        _event_data(
            date=None,
            datetime=None,
            original_phrase="03/04/2026",
            evidence={"quote": "Deadline: 03/04/2026", "page_number": 1},
        )
    )

    assert event.date is None
    assert event.datetime is None
    assert event.original_phrase == "03/04/2026"


def test_relative_date_cannot_be_calculated_without_reference_point() -> None:
    with pytest.raises(ValidationError, match="explicit reference_date"):
        TemporalEventExtraction.model_validate(
            _event_data(
                date="2026-07-15",
                original_phrase="within 14 days",
                reference_date=None,
            )
        )

    unresolved = TemporalEventExtraction.model_validate(
        _event_data(
            date=None,
            datetime=None,
            original_phrase="within 14 days",
            reference_date=None,
        )
    )
    resolved = TemporalEventExtraction.model_validate(
        _event_data(
            date="2026-07-15",
            original_phrase="within 14 days",
            reference_date="2026-07-01",
        )
    )

    assert unresolved.date is None
    assert resolved.reference_date == date(2026, 7, 1)


def test_standard_ai_processing_requests_and_preserves_temporal_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeResponses:
        def parse(self, **kwargs: object) -> SimpleNamespace:
            captured.update(kwargs)
            return SimpleNamespace(
                output_parsed=_document_extraction_data(
                    temporal_events=[_event_data()]
                ),
                id="resp_temporal_123",
                usage=None,
            )

    monkeypatch.setattr(
        ai_processing,
        "create_openai_client",
        lambda: SimpleNamespace(responses=FakeResponses()),
    )

    result = ai_processing.run_standard_ai_processing(
        raw_text="Bitte zahlen Sie bis zum 31.07.2026.",
        original_filename="invoice.pdf",
    )

    system_prompt = captured["input"][0]["content"]  # type: ignore[index]
    assert "at most 20 temporal_events" in system_prompt
    assert "return null for both date and datetime" in system_prompt
    assert "explicit reference point" in system_prompt
    assert captured["text_format"] is DocumentAIExtraction
    assert result.extracted_data.temporal_events[0].source_field == "due_date"
    assert result.extracted_data.model_dump(mode="json")["temporal_events"][0][
        "date"
    ] == "2026-07-31"


def test_temporal_evidence_requests_page_aware_text_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extraction = DocumentAIExtraction.model_validate(
        _document_extraction_data(temporal_events=[_event_data()])
    )
    document = SimpleNamespace(id=123)
    expected_pages = [SimpleNamespace(page_number=1, text="source")]
    seen: list[object] = []

    def fake_extract_pages(candidate: object) -> list[SimpleNamespace]:
        seen.append(candidate)
        return expected_pages

    monkeypatch.setattr(
        document_tasks,
        "extract_text_pages_from_document",
        fake_extract_pages,
    )

    pages = document_tasks._source_pages_for_evidence(
        document=document,  # type: ignore[arg-type]
        ai_result=SimpleNamespace(extracted_data=extraction),  # type: ignore[arg-type]
    )

    assert pages == expected_pages
    assert seen == [document]


def test_processing_result_persists_temporal_events_in_document_json() -> None:
    extraction = DocumentAIExtraction.model_validate(
        _document_extraction_data(temporal_events=[_event_data()])
    )
    ai_result = StandardAIProcessingResult(
        model="gpt-4o-mini",
        response_id="resp_temporal_123",
        extracted_data=extraction,
        usage=OpenAIUsage(input_tokens=10, output_tokens=5, total_tokens=15),
    )
    validation_result = ExtractionValidationResult(
        status="valid",
        score=100,
        ocr_quality_score=100,
    )
    temporal_validation = validate_temporal_events(
        extraction=extraction,
        raw_text="Bitte zahlen Sie bis zum 31.07.2026.",
        source_pages=[
            ExtractedTextPage(
                page_number=2,
                text="Bitte zahlen Sie bis zum 31.07.2026.",
            )
        ],
    )
    document = SimpleNamespace(id=123, owner_id=456)

    class FakeSession:
        def __init__(self) -> None:
            self.added: list[object] = []

        def add(self, value: object) -> None:
            self.added.append(value)

    db = FakeSession()

    document_tasks._apply_standard_ai_processing_result(
        db=db,  # type: ignore[arg-type]
        document=document,  # type: ignore[arg-type]
        ai_result=ai_result,
        validation_result=validation_result,
        temporal_validation=temporal_validation,
    )

    assert document.ai_extracted_data["due_date"] == "2026-07-31"
    assert document.ai_extracted_data["action_deadline"] == "2026-07-31"
    assert document.ai_extracted_data["temporal_events"][0]["date"] == "2026-07-31"
    assert document.ai_extracted_data["temporal_events"][0]["evidence"] == {
        "quote": "Bitte zahlen Sie bis zum 31.07.2026.",
        "page_number": 2,
    }
    assert document.validation_candidates["temporal_validation"]["events"][0][
        "status"
    ] == "valid"
    assert len(db.added) == 2
