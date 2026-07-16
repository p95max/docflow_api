from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas.ai_processing import DocumentAIExtraction
from app.services.extraction_validation import validate_ai_extraction
from app.services.text_extraction import ExtractedTextPage


def _extraction(**overrides: object) -> DocumentAIExtraction:
    data: dict[str, object] = {
        "document_type": "invoice",
        "summary": "Invoice for services.",
        "sender": "Example GmbH",
        "recipient": None,
        "document_date": "2026-07-01",
        "due_date": "2026-07-31",
        "total_amount": 1250.0,
        "currency": "eur",
        "invoice_number": "INV-1",
        "reference_number": None,
        "requires_action": True,
        "action_deadline": None,
        "confidence_score": 0.9,
        "notes": None,
    }
    data.update(overrides)
    return DocumentAIExtraction.model_validate(data)


def test_schema_forbids_unknown_fields_and_normalizes_currency() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _extraction(unexpected_field="must not be persisted")

    assert _extraction().currency == "EUR"


def test_validation_accepts_german_amount_and_grounded_dates() -> None:
    source_text = """
        Rechnung von Example GmbH\n
        Rechnungsdatum: 01.07.2026\n
        Zahlbar bis: 31. Juli 2026\n
        Gesamtbetrag: 1.250,00 EUR
    """
    result = validate_ai_extraction(
        extraction=_extraction(
            evidence={
                "amount": {"quote": "Gesamtbetrag: 1.250,00 EUR", "page_number": 2},
                "currency": {"quote": "Gesamtbetrag: 1.250,00 EUR", "page_number": 2},
                "document_date": {"quote": "Rechnungsdatum: 01.07.2026", "page_number": 2},
                "due_date": {"quote": "Zahlbar bis: 31. Juli 2026", "page_number": 2},
                "sender": {"quote": "Rechnung von Example GmbH", "page_number": 2},
            },
        ),
        raw_text=source_text,
        source_pages=[ExtractedTextPage(page_number=2, text=source_text)],
        today=date(2026, 7, 15),
    )

    assert result.status == "valid"
    assert result.score == 100
    assert result.errors == []
    assert set(result.evidence) == {
        "amount",
        "currency",
        "document_date",
        "due_date",
        "sender",
    }


def test_validation_marks_ungrounded_and_inconsistent_critical_fields_for_review() -> None:
    result = validate_ai_extraction(
        extraction=_extraction(
            total_amount=999.0,
            due_date="2026-06-30",
            currency=None,
        ),
        raw_text="Example GmbH\n2026-07-01\nTotal: 1,250.00 EUR",
        today=date(2026, 7, 15),
    )

    assert result.status == "needs_review"
    assert result.score < 100
    assert "Amount is not confirmed" in " ".join(result.errors)
    assert "Due date is not confirmed" in " ".join(result.errors)
    assert "Amount and currency" in " ".join(result.errors)


def test_validation_rejects_evidence_from_another_page() -> None:
    result = validate_ai_extraction(
        extraction=_extraction(
            evidence={
                "amount": {"quote": "Total: 1,250.00 EUR", "page_number": 2},
                "currency": {"quote": "Total: 1,250.00 EUR", "page_number": 2},
                "document_date": {"quote": "2026-07-01", "page_number": 1},
                "due_date": {"quote": "2026-07-31", "page_number": 1},
                "sender": {"quote": "Example GmbH", "page_number": 1},
            },
        ),
        raw_text="Example GmbH\n2026-07-01\n2026-07-31\nTotal: 1,250.00 EUR",
        source_pages=[
            ExtractedTextPage(
                page_number=1,
                text="Example GmbH\n2026-07-01\n2026-07-31",
            ),
            ExtractedTextPage(page_number=2, text="No financial values here."),
        ],
        today=date(2026, 7, 15),
    )

    assert result.status == "needs_review"
    assert "Amount evidence does not match" in " ".join(result.errors)
    assert "amount" not in result.evidence
