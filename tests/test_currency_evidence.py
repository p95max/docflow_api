from app.schemas.ai_processing import DocumentAIExtraction
from app.services.extraction_validation import validate_ai_extraction
from app.services.text_extraction import ExtractedTextPage


def test_aud_currency_is_inferred_from_australian_invoice_context() -> None:
    source = """Denny Gunawan
221 Queen St
Melbourne VIC 3000
$39.60
Invoice Number: #20130304
Total
$39.60
"""
    extraction = DocumentAIExtraction.model_validate(
        {
            "document_type": "invoice",
            "summary": None,
            "sender": "Denny Gunawan",
            "recipient": None,
            "document_date": None,
            "due_date": None,
            "total_amount": 39.6,
            "currency": "AUD",
            "invoice_number": "#20130304",
            "reference_number": None,
            "requires_action": False,
            "action_deadline": None,
            "confidence_score": 0.9,
            "notes": None,
            "evidence": {
                "amount": {"quote": "$39.60", "page_number": 1},
                "currency": {"quote": "$39.60", "page_number": 1},
                "sender": {"quote": "Denny Gunawan", "page_number": 1},
            },
        }
    )

    result = validate_ai_extraction(
        extraction=extraction,
        raw_text=source,
        source_pages=[ExtractedTextPage(page_number=1, text=source)],
    )

    assert result.status == "warning"
    assert result.errors == []
    assert result.score == 92
    assert result.evidence["currency"].evidence_type == "inferred"
    assert result.evidence["currency"].reason is not None
    assert "Australian address" in result.evidence["currency"].reason
    assert "Currency AUD was inferred" in " ".join(result.warnings)


def test_ambiguous_dollar_currency_without_regional_context_needs_review() -> None:
    source = "Vendor\nTotal\n$39.60"
    extraction = DocumentAIExtraction.model_validate(
        {
            "document_type": "invoice",
            "summary": None,
            "sender": "Vendor",
            "recipient": None,
            "document_date": None,
            "due_date": None,
            "total_amount": 39.6,
            "currency": "AUD",
            "invoice_number": None,
            "reference_number": None,
            "requires_action": False,
            "action_deadline": None,
            "confidence_score": 0.7,
            "notes": None,
            "evidence": {
                "amount": {"quote": "$39.60", "page_number": 1},
                "currency": {"quote": "$39.60", "page_number": 1},
                "sender": {"quote": "Vendor", "page_number": 1},
            },
        }
    )

    result = validate_ai_extraction(extraction=extraction, raw_text=source)

    assert result.status == "needs_review"
    assert "does not support the extracted ISO currency" in " ".join(result.errors)
    assert "currency" not in result.evidence


def test_direct_iso_currency_evidence_remains_direct() -> None:
    source = "Vendor\nTotal: 39.60 AUD"
    extraction = DocumentAIExtraction.model_validate(
        {
            "document_type": "invoice",
            "summary": None,
            "sender": "Vendor",
            "recipient": None,
            "document_date": None,
            "due_date": None,
            "total_amount": 39.6,
            "currency": "AUD",
            "invoice_number": None,
            "reference_number": None,
            "requires_action": False,
            "action_deadline": None,
            "confidence_score": 0.9,
            "notes": None,
            "evidence": {
                "amount": {"quote": "Total: 39.60 AUD", "page_number": 1},
                "currency": {"quote": "Total: 39.60 AUD", "page_number": 1},
                "sender": {"quote": "Vendor", "page_number": 1},
            },
        }
    )

    result = validate_ai_extraction(extraction=extraction, raw_text=source)

    assert result.status == "valid"
    assert result.evidence["currency"].evidence_type == "direct"
    assert result.evidence["currency"].reason is None
