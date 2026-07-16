from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import app.services.ai_processing as ai_processing


def test_standard_ai_processing_rejects_malformed_structured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The OpenAI SDK result is validated again before it reaches persistence."""
    malformed_output = {
        "document_type": "not-a-supported-document-type",
        "summary": "Invoice",
        "sender": "Vendor",
        "recipient": None,
        "document_date": "2016-11-26",
        "due_date": None,
        "total_amount": 950.0,
        "currency": "USD",
        "invoice_number": "161126",
        "reference_number": None,
        "requires_action": True,
        "action_deadline": None,
        "confidence_score": 1.5,
        "notes": None,
    }

    class FakeResponses:
        def parse(self, **_: object) -> SimpleNamespace:
            return SimpleNamespace(output_parsed=malformed_output)

    monkeypatch.setattr(
        ai_processing,
        "create_openai_client",
        lambda: SimpleNamespace(responses=FakeResponses()),
    )

    with pytest.raises(ValidationError) as exc_info:
        ai_processing.run_standard_ai_processing(
            raw_text="Invoice number 161126. Total USD 950.00.",
            original_filename="invoice.pdf",
        )

    invalid_fields = {error["loc"][0] for error in exc_info.value.errors()}
    assert invalid_fields == {"document_type", "confidence_score"}


def test_standard_ai_processing_uses_explicit_fallback_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeResponses:
        def parse(self, **kwargs: object) -> SimpleNamespace:
            captured.update(kwargs)
            return SimpleNamespace(
                output_parsed={
                    "document_type": "letter",
                    "summary": None,
                    "sender": None,
                    "recipient": None,
                    "document_date": None,
                    "due_date": None,
                    "total_amount": None,
                    "currency": None,
                    "invoice_number": None,
                    "reference_number": None,
                    "requires_action": False,
                    "action_deadline": None,
                    "confidence_score": 0.5,
                    "notes": None,
                },
                id="resp_fallback",
                usage=None,
            )

    monkeypatch.setattr(
        ai_processing,
        "create_openai_client",
        lambda: SimpleNamespace(responses=FakeResponses()),
    )

    result = ai_processing.run_standard_ai_processing(
        raw_text="A letter.",
        original_filename="letter.pdf",
        model="gpt-4o",
    )

    assert captured["model"] == "gpt-4o"
    assert result.model == "gpt-4o"
