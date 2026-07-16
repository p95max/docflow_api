"""Independent, deterministic checks for AI document extraction results."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from app.schemas.ai_processing import DocumentAIExtraction, FieldEvidence

if TYPE_CHECKING:
    from app.services.text_extraction import ExtractedTextPage


VALIDATION_VALID = "valid"
VALIDATION_WARNING = "warning"
VALIDATION_NEEDS_REVIEW = "needs_review"
VALIDATION_FAILED = "failed"

_AMOUNT_CANDIDATE_PATTERN = re.compile(
    r"(?<![\w])(?:\d{1,3}(?:[.,\s]\d{3})+|\d+)(?:[,.]\d{2})(?!\w)"
)
_LOCALE_DATE_PATTERN = re.compile(
    r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{4})(?!\d)"
)
_GERMAN_MONTH_PATTERN = re.compile(
    r"(?<!\w)(\d{1,2})\.?\s+(januar|februar|märz|maerz|april|mai|juni|juli|august|september|oktober|november|dezember)\s+(\d{4})(?!\w)",
    re.IGNORECASE,
)
_GERMAN_MONTHS = {
    "januar": 1,
    "februar": 2,
    "märz": 3,
    "maerz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
}


class ExtractionValidationResult(BaseModel):
    """Persistable outcome of deterministic post-processing validation."""

    status: str = Field(
        pattern=r"^(valid|warning|needs_review|failed)$",
    )
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    score: float = Field(ge=0, le=100)
    evidence: dict[str, FieldEvidence] = Field(default_factory=dict)


def validate_ai_extraction(
    *,
    extraction: DocumentAIExtraction,
    raw_text: str,
    source_pages: list["ExtractedTextPage"] | None = None,
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

    if extraction.document_date and not _date_is_grounded(
        extraction.document_date,
        source,
    ):
        errors.append("Document date is not confirmed by the extracted document text.")

    if extraction.due_date and not _date_is_grounded(extraction.due_date, source):
        errors.append("Due date is not confirmed by the extracted document text.")

    if extraction.action_deadline and not _date_is_grounded(
        extraction.action_deadline,
        source,
    ):
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

    accepted_evidence = _validate_evidence(
        extraction=extraction,
        source=source,
        source_pages=source_pages,
        errors=errors,
    )
    _validate_currency_amount_link(
        extraction=extraction,
        accepted_evidence=accepted_evidence,
        errors=errors,
    )

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
        evidence=accepted_evidence,
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


def _date_is_grounded(value: str, source: str) -> bool:
    expected = _parse_iso_date(value)
    if expected is None:
        return False
    if value in source:
        return True

    for day, month, year in _LOCALE_DATE_PATTERN.findall(source):
        if _safe_date(year=year, month=month, day=day) == expected:
            return True

    for day, month_name, year in _GERMAN_MONTH_PATTERN.findall(source):
        month = _GERMAN_MONTHS[month_name.casefold()]
        if _safe_date(year=year, month=str(month), day=day) == expected:
            return True
    return False


def _safe_date(*, year: str, month: str, day: str) -> date | None:
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def _text_value_is_grounded(value: str, source: str) -> bool:
    normalized_value = " ".join(value.casefold().split())
    normalized_source = " ".join(source.casefold().split())
    return normalized_value in normalized_source


def _validate_evidence(
    *,
    extraction: DocumentAIExtraction,
    source: str,
    source_pages: list["ExtractedTextPage"] | None,
    errors: list[str],
) -> dict[str, FieldEvidence]:
    """Accept evidence only when its quote and page both match local text."""
    field_values: tuple[tuple[str, str | float | None], ...] = (
        ("amount", extraction.total_amount),
        ("currency", extraction.currency),
        ("document_date", extraction.document_date),
        ("due_date", extraction.due_date),
        ("action_deadline", extraction.action_deadline),
        ("sender", extraction.sender),
    )
    accepted: dict[str, FieldEvidence] = {}

    for field_name, field_value in field_values:
        if field_value is None:
            continue

        evidence = getattr(extraction.evidence, field_name)
        label = field_name.replace("_", " ")
        if evidence is None:
            errors.append(f"{label.capitalize()} has no source evidence.")
            continue

        page_text = _evidence_page_text(
            evidence=evidence,
            source=source,
            source_pages=source_pages,
        )
        if page_text is None or not _text_value_is_grounded(evidence.quote, page_text):
            errors.append(f"{label.capitalize()} evidence does not match the stated page.")
            continue

        if not _evidence_supports_value(
            field_name=field_name,
            field_value=field_value,
            quote=evidence.quote,
        ):
            errors.append(f"{label.capitalize()} evidence does not support the extracted value.")
            continue

        accepted[field_name] = evidence

    return accepted


def _evidence_page_text(
    *,
    evidence: FieldEvidence,
    source: str,
    source_pages: list["ExtractedTextPage"] | None,
) -> str | None:
    if source_pages is None:
        return source if evidence.page_number == 1 else None

    for page in source_pages:
        if page.page_number == evidence.page_number:
            return page.text
    return None


def _evidence_supports_value(
    *,
    field_name: str,
    field_value: str | float,
    quote: str,
) -> bool:
    if field_name == "amount":
        return _amount_is_grounded(float(field_value), quote)
    if field_name in {"document_date", "due_date", "action_deadline"}:
        return _date_is_grounded(str(field_value), quote)
    return _text_value_is_grounded(str(field_value), quote)


def _validate_currency_amount_link(
    *,
    extraction: DocumentAIExtraction,
    accepted_evidence: dict[str, FieldEvidence],
    errors: list[str],
) -> None:
    """Require the currency evidence to show the grounded total alongside it."""
    if extraction.total_amount is None or extraction.currency is None:
        return

    currency_evidence = accepted_evidence.get("currency")
    if currency_evidence is None:
        return

    if not _amount_is_grounded(extraction.total_amount, currency_evidence.quote):
        errors.append("Currency evidence must include the grounded total amount.")
