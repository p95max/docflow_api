from app.schemas.ai_processing import DocumentAIExtraction
from app.services.extraction_sanitization import (
    is_document_template,
    sanitize_ai_extraction,
)
from app.services.extraction_validation import validate_ai_extraction


def _extraction(*, sender: str | None) -> DocumentAIExtraction:
    return DocumentAIExtraction.model_validate(
        {
            "document_type": "letter",
            "summary": "Widerspruch gegen eine Rechnung.",
            "sender": sender,
            "recipient": None,
            "document_date": None,
            "due_date": None,
            "total_amount": None,
            "currency": None,
            "invoice_number": None,
            "reference_number": None,
            "requires_action": False,
            "action_deadline": None,
            "confidence_score": 0.8,
            "notes": None,
            "evidence": {
                "sender": {
                    "quote": "Michaela Muster, Musterweg 1, 99999 Musterstadt",
                    "page_number": 1,
                }
            },
        }
    )


def test_musterbrief_is_detected_and_sample_sender_is_removed() -> None:
    raw_text = """
    MUSTERBRIEF: WIDERSPRUCH GEGEN EINE RECHNUNG
    Absender:
    Michaela Muster
    Musterweg 1
    99999 Musterstadt
    [Rechnungsnummer] vom [xx.xx.20xx] über [Kostenbetrag]
    Kopieren Sie den Text in ein Textverarbeitungsprogramm.
    Ergänzen Sie ihn mit Ihren Absenderangaben und löschen Sie die Platzhalter.
    """
    original = _extraction(
        sender="Michaela Muster, Musterweg 1, 99999 Musterstadt"
    )

    sanitized = sanitize_ai_extraction(extraction=original, raw_text=raw_text)

    assert is_document_template(raw_text) is True
    assert original.sender is not None
    assert sanitized.sender is None
    assert sanitized.evidence.sender is None

    validation = validate_ai_extraction(
        extraction=sanitized,
        raw_text=raw_text,
    )
    assert validation.status == "warning"
    assert validation.score == 92
    assert validation.errors == []
    assert "Document template detected" in " ".join(validation.warnings)


def test_real_sender_is_preserved_for_non_template_document() -> None:
    raw_text = "Example GmbH\nInvoice total: 125.00 EUR"
    original = _extraction(sender="Example GmbH")

    sanitized = sanitize_ai_extraction(extraction=original, raw_text=raw_text)

    assert sanitized.sender == "Example GmbH"
    assert sanitized.evidence.sender is not None
