"""Runtime extensions for deterministic extraction validation.

Kept separate so regional/date rules stay isolated from the core validator.
"""

from __future__ import annotations

from datetime import date
import re
from typing import TYPE_CHECKING

from app.schemas.ai_processing import DocumentAIExtraction, FieldEvidence

if TYPE_CHECKING:
    from app.services.text_extraction import ExtractedTextPage

_ENGLISH_DATE_PATTERN = re.compile(
    r"(?<!\w)(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2}),?\s+(\d{4})(?!\w)",
    re.IGNORECASE,
)
_ENGLISH_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_DOLLAR_PATTERN = re.compile(r"\$\s*\d")
_AUSTRALIA_PATTERNS = (
    re.compile(r"\b(?:VIC|NSW|QLD|WA|SA|TAS|ACT|NT)\s+\d{4}\b", re.I),
    re.compile(r"\b(?:Australia|Melbourne|Sydney|Brisbane|Perth|Adelaide)\b", re.I),
)
_CANADA_PATTERNS = (
    re.compile(r"\bCanada\b", re.I),
    re.compile(r"\b[A-Z]\d[A-Z]\s?\d[A-Z]\d\b", re.I),
)
_NEW_ZEALAND_PATTERNS = (
    re.compile(r"\b(?:New Zealand|Auckland|Wellington|Christchurch)\b", re.I),
)
_US_PATTERNS = (
    re.compile(r"\b(?:United States|USA)\b", re.I),
    re.compile(
        r"\b(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WV|WI|WY|DC)\s+\d{5}(?:-\d{4})?\b",
        re.I,
    ),
)


def install() -> None:
    """Install idempotent extensions into the core validation module."""
    from app.services import extraction_validation as core

    if getattr(core, "_extensions_installed", False):
        return

    original_date_is_grounded = core._date_is_grounded
    original_validate = core.validate_ai_extraction

    def date_is_grounded(value: str, source: str) -> bool:
        if original_date_is_grounded(value, source):
            return True
        try:
            expected = date.fromisoformat(value)
        except ValueError:
            return False
        for month_name, day, year in _ENGLISH_DATE_PATTERN.findall(source):
            try:
                parsed = date(int(year), _ENGLISH_MONTHS[month_name.casefold()], int(day))
            except ValueError:
                continue
            if parsed == expected:
                return True
        return False

    def validate_ai_extraction(
        *,
        extraction: DocumentAIExtraction,
        raw_text: str,
        source_pages: list["ExtractedTextPage"] | None = None,
        today: date | None = None,
    ):
        result = original_validate(
            extraction=extraction,
            raw_text=raw_text,
            source_pages=source_pages,
            today=today,
        )
        if extraction.currency is None or "currency" in result.evidence:
            return result

        inferred = _infer_currency(raw_text)
        if inferred != extraction.currency.upper():
            return result

        evidence = _grounded_dollar_evidence(
            extraction=extraction,
            raw_text=raw_text,
            source_pages=source_pages,
        )
        if evidence is None:
            return result

        reason = _currency_reason(inferred)
        errors = [
            message
            for message in result.errors
            if not message.startswith("Currency evidence ")
            and message != "Currency has no source evidence."
        ]
        warnings = list(result.warnings)
        if reason not in warnings:
            warnings.append(reason)
        accepted_evidence = dict(result.evidence)
        accepted_evidence["currency"] = evidence.model_copy(
            update={"evidence_type": "inferred", "reason": reason}
        )
        score, status = _score_and_status(
            errors=errors,
            warnings=warnings,
            ambiguity_flags=result.ambiguity_flags,
            ocr_quality_score=result.ocr_quality_score,
        )
        return result.model_copy(
            update={
                "errors": errors,
                "warnings": warnings,
                "evidence": accepted_evidence,
                "score": score,
                "status": status,
            }
        )

    core._date_is_grounded = date_is_grounded
    core.validate_ai_extraction = validate_ai_extraction
    core._extensions_installed = True


def _grounded_dollar_evidence(
    *,
    extraction: DocumentAIExtraction,
    raw_text: str,
    source_pages: list["ExtractedTextPage"] | None,
) -> FieldEvidence | None:
    for evidence in (extraction.evidence.currency, extraction.evidence.amount):
        if evidence is None or "$" not in evidence.quote:
            continue
        page_text = _page_text(
            evidence=evidence,
            raw_text=raw_text,
            source_pages=source_pages,
        )
        if page_text and _normalize(evidence.quote) in _normalize(page_text):
            return evidence
    return None


def _page_text(
    *,
    evidence: FieldEvidence,
    raw_text: str,
    source_pages: list["ExtractedTextPage"] | None,
) -> str | None:
    if source_pages is None:
        return raw_text if evidence.page_number == 1 else None
    for page in source_pages:
        if page.page_number == evidence.page_number:
            return page.text
    return None


def _infer_currency(source: str) -> str | None:
    if not _DOLLAR_PATTERN.search(source):
        return None
    issuer = _issuer_block(source)
    issuer_currency = _unique_currency(issuer)
    return issuer_currency or _unique_currency(source)


def _issuer_block(source: str) -> str:
    match = re.search(
        r"(?ims)^\s*from\s*:\s*(.*?)(?=^\s*to\s*:|\Z)",
        source,
    )
    return match.group(1) if match else ""


def _unique_currency(source: str) -> str | None:
    if not source:
        return None
    matches = {
        "AUD": any(pattern.search(source) for pattern in _AUSTRALIA_PATTERNS),
        "CAD": any(pattern.search(source) for pattern in _CANADA_PATTERNS),
        "NZD": any(pattern.search(source) for pattern in _NEW_ZEALAND_PATTERNS),
        "USD": any(pattern.search(source) for pattern in _US_PATTERNS),
    }
    found = [currency for currency, matched in matches.items() if matched]
    return found[0] if len(found) == 1 else None


def _currency_reason(currency: str) -> str:
    region = {
        "AUD": "Australian issuer/document",
        "CAD": "Canadian issuer/document",
        "NZD": "New Zealand issuer/document",
        "USD": "United States issuer",
    }[currency]
    return (
        f"Currency {currency} was inferred from the {region} context because "
        'the document uses the ambiguous "$" symbol.'
    )


def _score_and_status(
    *,
    errors: list[str],
    warnings: list[str],
    ambiguity_flags: list[str],
    ocr_quality_score: float,
) -> tuple[float, str]:
    score = max(
        0.0,
        100.0
        - len(errors) * 20.0
        - len(warnings) * 8.0
        - len(ambiguity_flags) * 12.0
        - max(0.0, 80.0 - ocr_quality_score) * 0.25,
    )
    if errors or ambiguity_flags or ocr_quality_score < 80:
        return score, "needs_review"
    if warnings:
        return score, "warning"
    return score, "valid"


def _normalize(value: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", value.casefold()).split())
