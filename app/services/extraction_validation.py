"""Independent, deterministic checks for AI document extraction results."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import re
from typing import TYPE_CHECKING, Literal

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
_CURRENCY_PATTERN = re.compile(
    r"\b(EUR|USD|GBP|CHF|AUD|CAD|NZD|PLN|SEK|NOK|DKK)\b",
    re.IGNORECASE,
)
_ISO_DATE_PATTERN = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_TEMPLATE_PLACEHOLDER_PATTERN = re.compile(r"\[[^\]\n]{2,80}\]")
_TEMPLATE_EXAMPLE_VALUE_PATTERN = re.compile(
    r"\b(?:muster(?:mann|frau|stadt|weg|straße|strasse)?|beispiel(?:name|stadt|straße|strasse|weg)?)\b",
    re.IGNORECASE,
)
_TEMPLATE_MARKERS = (
    "musterbrief",
    "so verwenden sie diesen musterbrief",
    "kopieren sie den text",
    "ergänzen sie ihn mit ihren absenderangaben",
    "ergaenzen sie ihn mit ihren absenderangaben",
    "löschen sie die kursiven platzhalter",
    "loeschen sie die kursiven platzhalter",
    "bitte senden sie den brief nicht an",
)
_DOLLAR_SYMBOL_PATTERN = re.compile(r"(?<![A-Z])\$\s*\d", re.IGNORECASE)
_AUSTRALIAN_CONTEXT_PATTERNS = (
    re.compile(r"\b(?:VIC|NSW|QLD|WA|SA|TAS|ACT|NT)\s+\d{4}\b", re.IGNORECASE),
    re.compile(r"\bAustralia\b", re.IGNORECASE),
    re.compile(r"\bMelbourne\b", re.IGNORECASE),
    re.compile(r"\bSydney\b", re.IGNORECASE),
    re.compile(r"\bBrisbane\b", re.IGNORECASE),
    re.compile(r"\bPerth\b", re.IGNORECASE),
    re.compile(r"\bAdelaide\b", re.IGNORECASE),
)
_CANADIAN_CONTEXT_PATTERNS = (
    re.compile(r"\bCanada\b", re.IGNORECASE),
    re.compile(r"\b[A-Z]\d[A-Z]\s?\d[A-Z]\d\b", re.IGNORECASE),
)
_NEW_ZEALAND_CONTEXT_PATTERNS = (
    re.compile(r"\bNew Zealand\b", re.IGNORECASE),
    re.compile(r"\bAuckland\b", re.IGNORECASE),
    re.compile(r"\bWellington\b", re.IGNORECASE),
    re.compile(r"\bChristchurch\b", re.IGNORECASE),
)


class AmountCandidate(BaseModel):
    value: float = Field(gt=0)
    currency: str | None = None
    label: Literal["total", "tax", "net", "outstanding", "other"]


class DateCandidate(BaseModel):
    value: str
    label: Literal["issue_date", "deadline", "service_date", "other"]


class ExtractionValidationResult(BaseModel):
    """Persistable outcome of deterministic post-processing validation."""

    status: str = Field(pattern=r"^(valid|warning|needs_review|failed)$")
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    score: float = Field(ge=0, le=100)
    evidence: dict[str, FieldEvidence] = Field(default_factory=dict)
    amount_candidates: list[AmountCandidate] = Field(default_factory=list)
    date_candidates: list[DateCandidate] = Field(default_factory=list)
    ambiguity_flags: list[str] = Field(default_factory=list)
    ocr_quality_score: float = Field(ge=0, le=100)


def _add_once(messages: list[str], message: str) -> None:
    if message not in messages:
        messages.append(message)


def validate_ai_extraction(
    *,
    extraction: DocumentAIExtraction,
    raw_text: str,
    source_pages: list["ExtractedTextPage"] | None = None,
    today: date | None = None,
) -> ExtractionValidationResult:
    """Validate AI fields against source text and cross-field business rules."""

    errors: list[str] = []
    warnings: list[str] = []
    source = raw_text.strip()
    is_template = _is_document_template(source)
    sender_is_template_example = bool(
        is_template
        and extraction.sender
        and _looks_like_template_example(extraction.sender)
    )

    if is_template:
        warnings.append(
            "Document template detected; sample values and placeholders must not be stored as real metadata."
        )

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

    if sender_is_template_example:
        errors.append("Sender appears to be example data from a document template.")
    elif extraction.sender and not _text_value_is_grounded(extraction.sender, source):
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

    skipped_evidence_fields = {"sender"} if sender_is_template_example else set()
    accepted_evidence = _validate_evidence(
        extraction=extraction,
        source=source,
        source_pages=source_pages,
        errors=errors,
        warnings=warnings,
        skipped_fields=skipped_evidence_fields,
    )
    _validate_currency_amount_link(
        extraction=extraction,
        accepted_evidence=accepted_evidence,
        errors=errors,
    )

    amount_candidates = _extract_amount_candidates(source)
    date_candidates = _extract_date_candidates(source)
    ambiguity_flags = _detect_ambiguity(
        amount_candidates=amount_candidates,
        date_candidates=date_candidates,
    )
    for flag in ambiguity_flags:
        _add_once(warnings, _ambiguity_message(flag))

    ocr_quality_score = _calculate_ocr_quality_score(source)
    if ocr_quality_score < 80:
        _add_once(
            warnings,
            "OCR text quality is low; review critical extracted fields.",
        )

    score = max(
        0.0,
        100.0
        - len(errors) * 20.0
        - len(warnings) * 8.0
        - len(ambiguity_flags) * 12.0
        - max(0.0, 80.0 - ocr_quality_score) * 0.25,
    )
    status = (
        VALIDATION_NEEDS_REVIEW
        if errors or ambiguity_flags or ocr_quality_score < 80
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
        amount_candidates=amount_candidates,
        date_candidates=date_candidates,
        ambiguity_flags=ambiguity_flags,
        ocr_quality_score=ocr_quality_score,
    )


def _is_document_template(source: str) -> bool:
    normalized = source.casefold()
    if "musterbrief" in normalized:
        return True
    marker_count = sum(marker in normalized for marker in _TEMPLATE_MARKERS)
    placeholder_count = len(_TEMPLATE_PLACEHOLDER_PATTERN.findall(source))
    return marker_count >= 2 or (marker_count >= 1 and placeholder_count >= 2)


def _looks_like_template_example(value: str) -> bool:
    return _TEMPLATE_EXAMPLE_VALUE_PATTERN.search(value) is not None


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
        normalized = compact.replace(thousands_separator, "").replace(
            decimal_separator,
            ".",
        )
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


def _normalize_grounding_text(value: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", value.casefold()).split())


def _text_value_is_grounded(value: str, source: str) -> bool:
    normalized_value = _normalize_grounding_text(value)
    normalized_source = _normalize_grounding_text(source)
    return bool(normalized_value) and normalized_value in normalized_source


def _validate_evidence(
    *,
    extraction: DocumentAIExtraction,
    source: str,
    source_pages: list["ExtractedTextPage"] | None,
    errors: list[str],
    warnings: list[str],
    skipped_fields: set[str] | None = None,
) -> dict[str, FieldEvidence]:
    field_values: tuple[tuple[str, str | float | None], ...] = (
        ("amount", extraction.total_amount),
        ("currency", extraction.currency),
        ("document_date", extraction.document_date),
        ("due_date", extraction.due_date),
        ("action_deadline", extraction.action_deadline),
        ("sender", extraction.sender),
    )
    accepted: dict[str, FieldEvidence] = {}
    skipped = skipped_fields or set()

    for field_name, field_value in field_values:
        if field_value is None or field_name in skipped:
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

        if field_name == "currency":
            accepted_currency = _validated_currency_evidence(
                currency=str(field_value),
                evidence=evidence,
                source=source,
            )
            if accepted_currency is None:
                errors.append(
                    "Currency evidence does not support the extracted ISO currency."
                )
                continue
            accepted[field_name] = accepted_currency
            if accepted_currency.evidence_type == "inferred":
                _add_once(
                    warnings,
                    accepted_currency.reason
                    or "Currency was inferred from regional document context.",
                )
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


def _validated_currency_evidence(
    *,
    currency: str,
    evidence: FieldEvidence,
    source: str,
) -> FieldEvidence | None:
    normalized_currency = currency.upper()
    if _text_value_is_grounded(normalized_currency, evidence.quote):
        return evidence.model_copy(update={"evidence_type": "direct", "reason": None})

    if "$" not in evidence.quote:
        return None

    inferred_currency = _infer_dollar_currency(source)
    if inferred_currency != normalized_currency:
        return None

    reason = {
        "AUD": (
            "Currency AUD was inferred from the Australian address because "
            'the document uses the ambiguous "$" symbol.'
        ),
        "CAD": (
            "Currency CAD was inferred from the Canadian address because "
            'the document uses the ambiguous "$" symbol.'
        ),
        "NZD": (
            "Currency NZD was inferred from the New Zealand address because "
            'the document uses the ambiguous "$" symbol.'
        ),
    }.get(normalized_currency)
    if reason is None:
        return None

    return evidence.model_copy(
        update={
            "evidence_type": "inferred",
            "reason": reason,
        }
    )


def _infer_dollar_currency(source: str) -> str | None:
    if not _DOLLAR_SYMBOL_PATTERN.search(source):
        return None

    matches = {
        "AUD": any(pattern.search(source) for pattern in _AUSTRALIAN_CONTEXT_PATTERNS),
        "CAD": any(pattern.search(source) for pattern in _CANADIAN_CONTEXT_PATTERNS),
        "NZD": any(pattern.search(source) for pattern in _NEW_ZEALAND_CONTEXT_PATTERNS),
    }
    inferred = [currency for currency, matched in matches.items() if matched]
    return inferred[0] if len(inferred) == 1 else None


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
    if extraction.total_amount is None or extraction.currency is None:
        return

    currency_evidence = accepted_evidence.get("currency")
    if currency_evidence is None:
        return

    if not _amount_is_grounded(extraction.total_amount, currency_evidence.quote):
        errors.append("Currency evidence must include the grounded total amount.")


def _extract_amount_candidates(source: str) -> list[AmountCandidate]:
    candidates: list[AmountCandidate] = []
    seen: set[tuple[Decimal, str | None, str]] = set()
    for match in _AMOUNT_CANDIDATE_PATTERN.finditer(source):
        value = _parse_amount_candidate(match.group(0))
        if value is None or value <= 0:
            continue
        context = _line_context(source, start=match.start(), end=match.end())
        currency_match = _CURRENCY_PATTERN.search(context)
        currency = currency_match.group(1).upper() if currency_match else None
        label = _amount_label(context)
        key = (value, currency, label)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            AmountCandidate(value=float(value), currency=currency, label=label)
        )
    return candidates


def _amount_label(
    context: str,
) -> Literal["total", "tax", "net", "outstanding", "other"]:
    normalized = context.casefold()
    if any(term in normalized for term in ("total", "gesamtbetrag", "endbetrag", "summe")):
        return "total"
    if any(term in normalized for term in ("vat", "tax", "mwst", "steuer")):
        return "tax"
    if any(term in normalized for term in ("subtotal", "net", "zwischensumme")):
        return "net"
    if any(
        term in normalized
        for term in ("outstanding", "amount due", "fällig", "offen", "zu zahlen")
    ):
        return "outstanding"
    return "other"


def _extract_date_candidates(source: str) -> list[DateCandidate]:
    candidates: list[DateCandidate] = []
    seen: set[tuple[date, str]] = set()
    parsed_candidates: list[tuple[date, int, int]] = []

    for match in _ISO_DATE_PATTERN.finditer(source):
        parsed = _safe_date(
            year=match.group(1),
            month=match.group(2),
            day=match.group(3),
        )
        if parsed:
            parsed_candidates.append((parsed, match.start(), match.end()))
    for match in _LOCALE_DATE_PATTERN.finditer(source):
        parsed = _safe_date(
            year=match.group(3),
            month=match.group(2),
            day=match.group(1),
        )
        if parsed:
            parsed_candidates.append((parsed, match.start(), match.end()))
    for match in _GERMAN_MONTH_PATTERN.finditer(source):
        parsed = _safe_date(
            year=match.group(3),
            month=str(_GERMAN_MONTHS[match.group(2).casefold()]),
            day=match.group(1),
        )
        if parsed:
            parsed_candidates.append((parsed, match.start(), match.end()))

    for parsed, start, end in parsed_candidates:
        label = _date_label(_line_context(source, start=start, end=end))
        key = (parsed, label)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(DateCandidate(value=parsed.isoformat(), label=label))
    return candidates


def _date_label(
    context: str,
) -> Literal["issue_date", "deadline", "service_date", "other"]:
    normalized = context.casefold()
    if any(term in normalized for term in ("due", "deadline", "zahlbar", "fällig", "frist")):
        return "deadline"
    if any(term in normalized for term in ("service", "leistung", "period")):
        return "service_date"
    if any(
        term in normalized
        for term in ("invoice date", "document date", "rechnungsdatum", "dated")
    ):
        return "issue_date"
    return "other"


def _line_context(source: str, *, start: int, end: int) -> str:
    line_start = source.rfind("\n", 0, start) + 1
    line_end = source.find("\n", end)
    if line_end == -1:
        line_end = len(source)
    return source[line_start:line_end]


def _detect_ambiguity(
    *,
    amount_candidates: list[AmountCandidate],
    date_candidates: list[DateCandidate],
) -> list[str]:
    flags: list[str] = []
    for label in ("total", "outstanding"):
        values = {
            candidate.value
            for candidate in amount_candidates
            if candidate.label == label
        }
        if len(values) > 1:
            flags.append("multiple_amount_candidates")
            break
    for label in ("issue_date", "deadline"):
        values = {
            candidate.value
            for candidate in date_candidates
            if candidate.label == label
        }
        if len(values) > 1:
            flags.append("multiple_date_candidates")
            break
    return flags


def _ambiguity_message(flag: str) -> str:
    return {
        "multiple_amount_candidates": "Multiple plausible total amounts were found; review the selected amount.",
        "multiple_date_candidates": "Multiple plausible dates were found; review the selected dates.",
    }[flag]


def _calculate_ocr_quality_score(source: str) -> float:
    if not source:
        return 0.0
    non_whitespace = [character for character in source if not character.isspace()]
    if not non_whitespace:
        return 0.0

    suspicious_characters = sum(
        character == "\ufffd" or (not character.isprintable())
        for character in non_whitespace
    )
    nonempty_lines = [line for line in source.splitlines() if line.strip()]
    short_lines = sum(1 for line in nonempty_lines if len(line.strip()) == 1)
    suspicious_ratio = suspicious_characters / len(non_whitespace)
    fragmented_ratio = short_lines / max(1, len(nonempty_lines))
    return round(
        max(0.0, 100.0 - suspicious_ratio * 300.0 - fragmented_ratio * 35.0),
        2,
    )
