"""Independent, deterministic checks for AI document extraction results."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import re

from pydantic import BaseModel, Field

from app.schemas.ai_processing import DocumentAIExtraction


VALIDATION_VALID = "valid"
VALIDATION_WARNING = "warning"
VALIDATION_NEEDS_REVIEW = "needs_review"
VALIDATION_FAILED = "failed"

_AMOUNT_CANDIDATE_PATTERN = re.compile(
    r"(?<![\w])(?:\d{1,3}(?:[.,\s]\d{3})+|\d+)(?:[,.]\d{2})(?!\w)"
)


class ExtractionValidationResult(BaseModel):
    """Persistable outcome of deterministic post-processing validation."""

    status: str = Field(
        pattern=r"^(valid|warning|needs_review|failed)$",
    )
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    score: float = Field(ge=0, le=100)


def validate_ai_extraction(
    *,
    extraction: DocumentAIExtraction,
    raw_text: str,
    today: date | None = None,
) -> ExtractionValidationResult:
    """Validate AI fields against source text and cross-field business rules.

    The function deliberately does not overwrite model output. A result needing
    review remains visible to the owner, together with the deterministic reason.
    """
    errors: list[str] = []
    warnings: list[str] = []
    source = raw_text.strip()

    if extraction.total_amount is not None and not _amount_is_grounded(
        extraction.total_amount,
        source,
    ):
        errors.append("Amount is not confirmed by the extracted document text.")

    if extraction.document_date and extraction.document_date not in source:
        errors.append("Document date is not confirmed by the extracted document text.")

    if extraction.due_date and extraction.due_date not in source:
        errors.append("Due date is not confirmed by the extracted document text.")

    if extraction.action_deadline and extraction.action_deadline not in source:
        errors.append("Action deadline is not confirmed by the extracted document text.")

    if extraction.sender and not _text_value_is_grounded(extraction.sender, source):
        warnings.append("Sender is not confirmed by the extracted document text.")

    if (extraction.total_amount is None) != (extraction.currency is None):
        errors.append("Amount and currency must be provided together.")

    document_date = _parse_iso_date(extraction.document_date)
    deadline = _parse_iso_date(extraction.action_deadline or extraction.due_date)
    if document_date and deadline and deadline < document_date:
        errors.append("Deadline cannot be earlier than the document date.")

    if document_date and document_date > (today or date.today()):
        warnings.append("Document date is in the future.")

    if extraction.document_type == "invoice":
        if extraction.sender is None:
            warnings.append("Invoice has no sender to review.")
        if extraction.total_amount is None:
            warnings.append("Invoice has no total amount to review.")

    score = max(0.0, 100.0 - len(errors) * 25.0 - len(warnings) * 10.0)
    status = (
        VALIDATION_NEEDS_REVIEW
        if errors
        else VALIDATION_WARNING
        if warnings
        else VALIDATION_VALID
    )
    return ExtractionValidationResult(
        status=status,
        errors=errors,
        warnings=warnings,
        score=score,
    )


def _amount_is_grounded(amount: float, source: str) -> bool:
    expected = _parse_amount_candidate(str(amount))
    if expected is None:
        return False
    return any(
        _parse_amount_candidate(match.group(0)) == expected
        for match in _AMOUNT_CANDIDATE_PATTERN.finditer(source)
    )


def _parse_amount_candidate(value: str) -> Decimal | None:
    compact = value.replace(" ", "")
    if "," in compact and "." in compact:
        decimal_separator = "," if compact.rfind(",") > compact.rfind(".") else "."
        thousands_separator = "." if decimal_separator == "," else ","
        normalized = compact.replace(thousands_separator, "").replace(decimal_separator, ".")
    elif "," in compact:
        normalized = compact.replace(",", ".")
    else:
        normalized = compact

    try:
        return Decimal(normalized).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _parse_iso_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _text_value_is_grounded(value: str, source: str) -> bool:
    normalized_value = " ".join(value.casefold().split())
    normalized_source = " ".join(source.casefold().split())
    return normalized_value in normalized_source
