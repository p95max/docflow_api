from app.schemas.ai_processing import DocumentAIExtraction
from app.services.extraction_sanitization import sanitize_ai_extraction


def _invoice(summary: str | None) -> DocumentAIExtraction:
    return DocumentAIExtraction.model_validate(
        {
            "document_type": "invoice",
            "summary": summary,
            "sender": "Denny Gunawan",
            "recipient": None,
            "document_date": None,
            "due_date": None,
            "total_amount": 39.6,
            "currency": "AUD",
            "invoice_number": "20130304",
            "reference_number": None,
            "requires_action": False,
            "action_deadline": None,
            "confidence_score": 0.9,
            "notes": None,
            "evidence": {},
        }
    )


def test_missing_invoice_summary_is_built_from_extracted_fields() -> None:
    sanitized = sanitize_ai_extraction(
        extraction=_invoice(None),
        raw_text="Denny Gunawan\nMelbourne VIC 3000\nOrganic Items\nTotal 39.60",
    )

    assert sanitized.summary == (
        "Invoice from Denny Gunawan for organic products totaling 39.60 AUD."
    )


def test_existing_ai_summary_is_not_replaced() -> None:
    sanitized = sanitize_ai_extraction(
        extraction=_invoice("Invoice for a grocery purchase."),
        raw_text="Organic Items\nTotal 39.60",
    )

    assert sanitized.summary == "Invoice for a grocery purchase."


def test_generic_invoice_summary_omits_unknown_subject() -> None:
    sanitized = sanitize_ai_extraction(
        extraction=_invoice("   "),
        raw_text="Denny Gunawan\nTotal 39.60",
    )

    assert sanitized.summary == "Invoice from Denny Gunawan totaling 39.60 AUD."
