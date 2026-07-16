from app.schemas.ai_processing import DocumentAIExtraction
from app.services.extraction_validation import validate_ai_extraction
from app.services.text_extraction import ExtractedTextPage


def _extraction(**overrides: object) -> DocumentAIExtraction:
    data: dict[str, object] = {
        "document_type": "invoice",
        "summary": None,
        "sender": "Denny Gunawan",
        "recipient": None,
        "document_date": None,
        "due_date": None,
        "total_amount": 39.6,
        "currency": "AUD",
        "invoice_number": None,
        "reference_number": None,
        "requires_action": False,
        "action_deadline": None,
        "confidence_score": 1.0,
        "notes": None,
        "evidence": {
            "amount": {"quote": "$39.60", "page_number": 1},
            "currency": {"quote": "AUD currency", "page_number": 1},
            "sender": {"quote": "Denny Gunawan", "page_number": 1},
        },
    }
    data.update(overrides)
    return DocumentAIExtraction.model_validate(data)


def test_bad_currency_quote_falls_back_to_grounded_amount_evidence() -> None:
    source = "Denny Gunawan\n221 Queen St\nMelbourne VIC 3000\nTotal\n$39.60"
    result = validate_ai_extraction(
        extraction=_extraction(),
        raw_text=source,
        source_pages=[ExtractedTextPage(page_number=1, text=source)],
    )

    assert result.status == "warning"
    assert result.errors == []
    assert result.evidence["currency"].quote == "$39.60"
    assert result.evidence["currency"].evidence_type == "inferred"


def test_english_dates_and_us_issuer_context_are_grounded() -> None:
    source = """Invoice
From:
DEMO - Sliced Invoices
Your City AZ 12345
Invoice Date
January 25, 2016
Due Date
January 31, 2016
Total Due
$93.50
To:
Test Business
Melbourne, VIC 3000
"""
    extraction = _extraction(
        sender="DEMO - Sliced Invoices",
        recipient="Test Business",
        document_date="2016-01-25",
        due_date="2016-01-31",
        total_amount=93.5,
        currency="USD",
        evidence={
            "amount": {"quote": "$93.50", "page_number": 1},
            "currency": {"quote": "$93.50", "page_number": 1},
            "document_date": {"quote": "January 25, 2016", "page_number": 1},
            "due_date": {"quote": "January 31, 2016", "page_number": 1},
            "sender": {"quote": "DEMO - Sliced Invoices", "page_number": 1},
        },
    )
    result = validate_ai_extraction(
        extraction=extraction,
        raw_text=source,
        source_pages=[ExtractedTextPage(page_number=1, text=source)],
    )

    assert result.status == "warning"
    assert result.errors == []
    assert result.evidence["currency"].evidence_type == "inferred"
    assert "document_date" in result.evidence
    assert "due_date" in result.evidence
    assert "United States issuer" in " ".join(result.warnings)
