"""Deterministic validation for AI-extracted temporal document candidates."""

from __future__ import annotations

from datetime import date
import re
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.ai_processing import DocumentAIExtraction, TemporalEventExtraction

if TYPE_CHECKING:
    from app.services.text_extraction import ExtractedTextPage


TEMPORAL_VALID = "valid"
TEMPORAL_WARNING = "warning"
TEMPORAL_NEEDS_REVIEW = "needs_review"
TEMPORAL_FAILED = "failed"

TemporalValidationStatus = Literal[
    "valid",
    "warning",
    "needs_review",
    "failed",
]
TemporalDateKind = Literal[
    "concrete",
    "relative",
    "template_placeholder",
    "ambiguous",
    "missing",
]
TemporalDateRole = Literal["event_date", "document_date", "unknown"]

_ISO_DATE_PATTERN = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_NUMERIC_EUROPEAN_DATE_PATTERN = re.compile(
    r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{4})(?!\d)"
)
_GERMAN_DATE_PATTERN = re.compile(
    r"(?<!\w)(\d{1,2})\.?\s+"
    r"(januar|februar|märz|maerz|april|mai|juni|juli|august|september|"
    r"oktober|november|dezember)\s+(\d{4})(?!\w)",
    re.IGNORECASE,
)
_ENGLISH_MONTH_FIRST_DATE_PATTERN = re.compile(
    r"(?<!\w)(january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\s+(\d{1,2}),?\s+(\d{4})(?!\w)",
    re.IGNORECASE,
)
_ENGLISH_DAY_FIRST_DATE_PATTERN = re.compile(
    r"(?<!\w)(\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\s+(\d{4})(?!\w)",
    re.IGNORECASE,
)
_RELATIVE_DATE_PATTERN = re.compile(
    r"(?:\bwithin\s+\d+\s+(?:calendar\s+|business\s+)?days?\b|"
    r"\b\d+\s+(?:calendar\s+|business\s+)?days?\s+(?:after|before|from)\b|"
    r"\b(?:innerhalb|binnen)\s+(?:von\s+)?\d+\s+tagen?\b|"
    r"\b\d+\s+tage?n?\s+(?:nach|vor)\b)",
    re.IGNORECASE,
)
_TEMPLATE_PLACEHOLDER_PATTERN = re.compile(
    r"\[[^\]\n]{1,80}\]|\b(?:xx|dd|tt)[./-](?:xx|mm)[./-](?:xx|yyyy|jjjj)\b",
    re.IGNORECASE,
)
_TEMPLATE_MARKERS = (
    "musterbrief",
    "sample template",
    "example template",
    "so verwenden sie diesen musterbrief",
    "kopieren sie den text",
)
_DOCUMENT_DATE_LABELS = (
    "document date",
    "invoice date",
    "rechnungsdatum",
    "ausstellungsdatum",
    "dated",
)
_EVENT_DATE_LABELS = (
    "due",
    "deadline",
    "payable",
    "payment",
    "zahlbar",
    "zahlen",
    "fällig",
    "faellig",
    "frist",
    "termin",
    "appointment",
    "expires",
    "expiry",
    "kündigung",
    "kuendigung",
    "end date",
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


class TemporalValidationIssue(BaseModel):
    """One stable, machine-readable validation finding."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[a-z0-9_]+$")
    severity: Literal["warning", "needs_review", "error"]
    message: str


class TemporalEventValidationResult(BaseModel):
    """Validation outcome for one temporal event candidate."""

    model_config = ConfigDict(extra="forbid")

    event_index: int = Field(ge=0)
    status: TemporalValidationStatus
    date_kind: TemporalDateKind
    date_role: TemporalDateRole
    resolved_date: date | None = None
    evidence_page_number: int = Field(ge=1)
    issue_codes: list[str] = Field(default_factory=list)
    issues: list[TemporalValidationIssue] = Field(default_factory=list)
    is_projectable: bool


class TemporalEventsValidationResult(BaseModel):
    """Persistable validation output for all temporal candidates in a document."""

    model_config = ConfigDict(extra="forbid")

    events: list[TemporalEventValidationResult] = Field(default_factory=list)


def validate_temporal_events(
    *,
    extraction: DocumentAIExtraction,
    raw_text: str,
    source_pages: list["ExtractedTextPage"] | None = None,
) -> TemporalEventsValidationResult:
    """Validate every AI temporal event against OCR text and page evidence."""
    document_date = _parse_iso_date(extraction.document_date)
    results = [
        validate_temporal_event(
            event=event,
            event_index=index,
            raw_text=raw_text,
            source_pages=source_pages,
            document_date=document_date,
        )
        for index, event in enumerate(extraction.temporal_events)
    ]
    _mark_conflicting_event_dates(events=extraction.temporal_events, results=results)
    return TemporalEventsValidationResult(events=results)


def validate_temporal_event(
    *,
    event: TemporalEventExtraction,
    event_index: int,
    raw_text: str,
    source_pages: list["ExtractedTextPage"] | None = None,
    document_date: date | None = None,
) -> TemporalEventValidationResult:
    """Validate one candidate without mutating the AI extraction result."""
    issues: list[TemporalValidationIssue] = []
    source = raw_text.strip()
    page_text = _evidence_page_text(
        page_number=event.evidence.page_number,
        raw_text=source,
        source_pages=source_pages,
    )
    date_kind = _classify_date_kind(event=event)
    date_role = _classify_date_role(event.evidence.quote)
    resolved_date = event.date or (event.datetime.date() if event.datetime else None)

    if _is_template_document(source) or _TEMPLATE_PLACEHOLDER_PATTERN.search(
        event.original_phrase
    ):
        date_kind = "template_placeholder"
        _add_issue(
            issues,
            code="template_placeholder",
            severity="error",
            message="Temporal candidates from document templates or placeholders are not usable.",
        )

    if page_text is None:
        _add_issue(
            issues,
            code="evidence_page_not_found",
            severity="error",
            message="Temporal evidence references a page that is not available in OCR text.",
        )
    elif not _text_contains(page_text, event.evidence.quote):
        _add_issue(
            issues,
            code="evidence_quote_not_found",
            severity="error",
            message="Temporal evidence quote is not present on the stated OCR page.",
        )

    if not _text_contains(event.evidence.quote, event.original_phrase):
        _add_issue(
            issues,
            code="original_phrase_not_in_evidence",
            severity="needs_review",
            message="The preserved temporal phrase is not contained in its evidence quote.",
        )

    quote_dates, has_ambiguous_numeric_date = _extract_dates(event.evidence.quote)
    if date_kind == "concrete":
        if resolved_date is None:
            _add_issue(
                issues,
                code="missing_event_date",
                severity="needs_review",
                message="A concrete temporal event has no resolved date or datetime.",
            )
        elif resolved_date not in quote_dates:
            _add_issue(
                issues,
                code="event_date_not_supported_by_evidence",
                severity="error",
                message="The temporal date is not supported by the evidence quote.",
            )

        if has_ambiguous_numeric_date:
            _add_issue(
                issues,
                code="ambiguous_numeric_date",
                severity="needs_review",
                message="The numeric date can be interpreted in more than one locale order.",
            )
        if len(quote_dates) > 1:
            _add_issue(
                issues,
                code="multiple_dates_in_evidence",
                severity="needs_review",
                message="The temporal evidence quote contains multiple distinct dates.",
            )

    if date_kind == "relative":
        if resolved_date is None:
            _add_issue(
                issues,
                code="relative_date_unresolved",
                severity="needs_review",
                message="A relative deadline has no resolved date.",
            )
        elif event.reference_date is None:
            _add_issue(
                issues,
                code="relative_date_missing_reference",
                severity="error",
                message="A relative deadline was resolved without an explicit reference date.",
            )
        elif event.reference_date not in _extract_dates(source)[0]:
            _add_issue(
                issues,
                code="relative_reference_not_grounded",
                severity="needs_review",
                message="The reference date for the relative deadline is not present in OCR text.",
            )

    if date_kind in {"ambiguous", "missing"}:
        _add_issue(
            issues,
            code="ambiguous_or_missing_date",
            severity="needs_review",
            message="The temporal candidate does not contain a concrete date or datetime.",
        )

    if date_role == "document_date" and event.source_field in {
        "due_date",
        "action_deadline",
        "appointment_date",
    }:
        _add_issue(
            issues,
            code="document_date_used_as_event",
            severity="needs_review",
            message="The evidence appears to describe a document date rather than an event date.",
        )
    elif date_role == "unknown" and date_kind == "concrete":
        _add_issue(
            issues,
            code="event_date_context_unclear",
            severity="warning",
            message="The evidence contains a date but no explicit event-date label.",
        )

    if document_date and resolved_date and resolved_date < document_date:
        _add_issue(
            issues,
            code="event_before_document_date",
            severity="error",
            message="An event deadline cannot be earlier than the document date.",
        )

    status = _status_from_issues(issues)
    return TemporalEventValidationResult(
        event_index=event_index,
        status=status,
        date_kind=date_kind,
        date_role=date_role,
        resolved_date=resolved_date,
        evidence_page_number=event.evidence.page_number,
        issue_codes=[issue.code for issue in issues],
        issues=issues,
        is_projectable=status in {TEMPORAL_VALID, TEMPORAL_WARNING},
    )


def _mark_conflicting_event_dates(
    *,
    events: list[TemporalEventExtraction],
    results: list[TemporalEventValidationResult],
) -> None:
    grouped: dict[str, list[tuple[TemporalEventExtraction, TemporalEventValidationResult]]] = {}
    for event, result in zip(events, results, strict=True):
        if result.resolved_date is None or result.status == TEMPORAL_FAILED:
            continue
        grouped.setdefault(event.source_field, []).append((event, result))

    for source_field, candidates in grouped.items():
        values = {result.resolved_date for _, result in candidates}
        if len(values) <= 1:
            continue
        for _, result in candidates:
            _add_issue(
                result.issues,
                code="conflicting_event_dates",
                severity="needs_review",
                message=(
                    f"Multiple temporal candidates for {source_field} have conflicting dates."
                ),
            )
            result.issue_codes = [issue.code for issue in result.issues]
            result.status = _status_from_issues(result.issues)
            result.is_projectable = result.status in {TEMPORAL_VALID, TEMPORAL_WARNING}


def _classify_date_kind(*, event: TemporalEventExtraction) -> TemporalDateKind:
    if _TEMPLATE_PLACEHOLDER_PATTERN.search(event.original_phrase):
        return "template_placeholder"
    if _RELATIVE_DATE_PATTERN.search(event.original_phrase):
        return "relative"
    if event.date is not None or event.datetime is not None:
        return "concrete"
    if event.original_phrase.strip():
        return "ambiguous"
    return "missing"


def _classify_date_role(quote: str) -> TemporalDateRole:
    normalized = quote.casefold()
    has_event_label = any(label in normalized for label in _EVENT_DATE_LABELS)
    has_document_label = any(label in normalized for label in _DOCUMENT_DATE_LABELS)
    if has_event_label:
        return "event_date"
    if has_document_label:
        return "document_date"
    return "unknown"


def _extract_dates(value: str) -> tuple[set[date], bool]:
    dates: set[date] = set()
    has_ambiguous_numeric_date = False

    for year, month, day in _ISO_DATE_PATTERN.findall(value):
        parsed = _safe_date(year=year, month=month, day=day)
        if parsed is not None:
            dates.add(parsed)

    for day, month, year in _NUMERIC_EUROPEAN_DATE_PATTERN.findall(value):
        parsed = _safe_date(year=year, month=month, day=day)
        if parsed is not None:
            dates.add(parsed)
            if int(day) <= 12 and int(month) <= 12:
                has_ambiguous_numeric_date = True

    for day, month_name, year in _GERMAN_DATE_PATTERN.findall(value):
        parsed = _safe_date(
            year=year,
            month=str(_GERMAN_MONTHS[month_name.casefold()]),
            day=day,
        )
        if parsed is not None:
            dates.add(parsed)

    for month_name, day, year in _ENGLISH_MONTH_FIRST_DATE_PATTERN.findall(value):
        parsed = _safe_date(
            year=year,
            month=str(_ENGLISH_MONTHS[month_name.casefold()]),
            day=day,
        )
        if parsed is not None:
            dates.add(parsed)

    for day, month_name, year in _ENGLISH_DAY_FIRST_DATE_PATTERN.findall(value):
        parsed = _safe_date(
            year=year,
            month=str(_ENGLISH_MONTHS[month_name.casefold()]),
            day=day,
        )
        if parsed is not None:
            dates.add(parsed)

    return dates, has_ambiguous_numeric_date


def _safe_date(*, year: str, month: str, day: str) -> date | None:
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def _parse_iso_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _evidence_page_text(
    *,
    page_number: int,
    raw_text: str,
    source_pages: list["ExtractedTextPage"] | None,
) -> str | None:
    if source_pages is None:
        return raw_text if page_number == 1 else None
    for page in source_pages:
        if page.page_number == page_number:
            return page.text
    return None


def _is_template_document(value: str) -> bool:
    normalized = value.casefold()
    return any(marker in normalized for marker in _TEMPLATE_MARKERS)


def _text_contains(haystack: str, needle: str) -> bool:
    normalized_needle = _normalize(needle)
    return bool(normalized_needle) and normalized_needle in _normalize(haystack)


def _normalize(value: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", value.casefold()).split())


def _add_issue(
    issues: list[TemporalValidationIssue],
    *,
    code: str,
    severity: Literal["warning", "needs_review", "error"],
    message: str,
) -> None:
    if any(issue.code == code for issue in issues):
        return
    issues.append(TemporalValidationIssue(code=code, severity=severity, message=message))


def _status_from_issues(issues: list[TemporalValidationIssue]) -> TemporalValidationStatus:
    severities = {issue.severity for issue in issues}
    if "error" in severities:
        return TEMPORAL_FAILED
    if "needs_review" in severities:
        return TEMPORAL_NEEDS_REVIEW
    if "warning" in severities:
        return TEMPORAL_WARNING
    return TEMPORAL_VALID
