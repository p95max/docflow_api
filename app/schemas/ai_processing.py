from __future__ import annotations

import re
from datetime import date as Date, datetime as DateTime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.timezones import validate_iana_timezone


DocumentType = Literal[
    "invoice",
    "receipt",
    "letter",
    "contract",
    "bank_statement",
    "tax_document",
    "medical_document",
    "other",
]

MAX_TEMPORAL_EVENTS_PER_DOCUMENT = 20

TemporalEventType = Literal[
    "payment_due",
    "response_deadline",
    "action_deadline",
    "appointment",
    "contract_start",
    "contract_end",
    "cancellation_deadline",
    "renewal",
]

TemporalEventSourceField = Literal[
    "due_date",
    "action_deadline",
    "contract_end",
    "appointment_date",
]

_RELATIVE_DATE_PHRASE_PATTERN = re.compile(
    r"(?:\bwithin\s+\d+\s+(?:calendar\s+|business\s+)?days?\b|"
    r"\b\d+\s+(?:calendar\s+|business\s+)?days?\s+(?:after|before|from)\b|"
    r"\b(?:after|before)\s+(?:receipt|delivery|issue|signing)\b|"
    r"\b(?:innerhalb|binnen)\s+(?:von\s+)?\d+\s+tagen?\b|"
    r"\b\d+\s+tage?n?\s+(?:nach|vor)\b)",
    re.IGNORECASE,
)


class TemporalEventEvidence(BaseModel):
    """Page-specific source evidence for one temporal candidate."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    quote: str = Field(
        min_length=1,
        max_length=500,
        description="Exact source quote supporting the temporal event.",
    )
    page_number: int = Field(
        ge=1,
        description="One-based page number containing the quote.",
    )


class TemporalEventExtraction(BaseModel):
    """Strict AI contract for one date or datetime found in a document."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event_type: TemporalEventType
    title: str = Field(min_length=1, max_length=255)
    date: Date | None = Field(
        default=None,
        description="ISO date for an all-day event; null when ambiguous.",
    )
    datetime: DateTime | None = Field(
        default=None,
        description=(
            "ISO datetime for a timed event; null when ambiguous. Include timezone "
            "information only when explicitly present in the document."
        ),
    )
    all_day: bool
    timezone: str | None = Field(
        default=None,
        max_length=64,
        description="IANA timezone only when explicitly stated in the document.",
    )
    requires_action: bool
    confidence_score: float = Field(ge=0.0, le=1.0)
    evidence: TemporalEventEvidence
    original_phrase: str = Field(
        min_length=1,
        max_length=500,
        description="Original date or deadline phrase exactly as written.",
    )
    source_field: TemporalEventSourceField
    reference_date: Date | None = Field(
        default=None,
        description=(
            "Explicit ISO reference date used to resolve a relative phrase; otherwise null."
        ),
    )

    @field_validator("timezone")
    @classmethod
    def timezone_must_be_iana(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_iana_timezone(value)

    @model_validator(mode="after")
    def validate_time_representation(self) -> TemporalEventExtraction:
        if self.all_day and self.datetime is not None:
            raise ValueError("All-day temporal events cannot include datetime.")
        if not self.all_day and self.date is not None:
            raise ValueError("Timed temporal events cannot include date.")
        if self.date is not None and self.datetime is not None:
            raise ValueError("Temporal events cannot include both date and datetime.")

        has_resolved_value = self.date is not None or self.datetime is not None
        if (
            has_resolved_value
            and _RELATIVE_DATE_PHRASE_PATTERN.search(self.original_phrase)
            and self.reference_date is None
        ):
            raise ValueError(
                "Relative temporal events require an explicit reference_date before "
                "date calculation."
            )
        return self


class DocumentAIExtraction(BaseModel):
    """The strictly bounded contract accepted from the AI extraction model."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    document_type: DocumentType = Field(description="Best matching document type.")
    summary: str | None = Field(
        max_length=5000,
        description="Short human-readable summary of the document.",
    )
    sender: str | None = Field(
        max_length=255,
        description="Sender, vendor, company, authority or person who issued the document.",
    )
    recipient: str | None = Field(
        max_length=255,
        description="Recipient name, company or person if available.",
    )
    document_date: str | None = Field(
        description="Document date in ISO format YYYY-MM-DD if available.",
    )
    due_date: str | None = Field(
        description="Payment due date, deadline or response deadline in ISO format YYYY-MM-DD if available.",
    )
    total_amount: float | None = Field(
        gt=0,
        description="Total amount if the document contains one.",
    )
    currency: str | None = Field(
        min_length=3,
        max_length=3,
        pattern=r"^[A-Za-z]{3}$",
        description="ISO currency code like EUR or USD if available.",
    )
    invoice_number: str | None = Field(
        max_length=120,
        description="Invoice number if available.",
    )
    reference_number: str | None = Field(
        max_length=120,
        description="Customer number, reference number, case number or similar identifier.",
    )
    requires_action: bool = Field(
        description="Whether the document requires user action.",
    )
    action_deadline: str | None = Field(
        description="Action deadline in ISO format YYYY-MM-DD if available.",
    )
    temporal_events: list[TemporalEventExtraction] = Field(
        default_factory=list,
        max_length=MAX_TEMPORAL_EVENTS_PER_DOCUMENT,
        description=(
            "Bounded temporal candidates grounded in the document. Keep ambiguous or "
            "unresolved relative dates with null date and datetime values."
        ),
    )
    confidence_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score between 0 and 1.",
    )
    notes: str | None = Field(
        max_length=2000,
        description="Important caveats or missing information.",
    )
    evidence: "DocumentExtractionEvidence" = Field(
        default_factory=lambda: DocumentExtractionEvidence(),
        description=(
            "Source evidence for critical fields. Direct evidence quotes the value itself. "
            "Inferred evidence may quote a symbol while deterministic validation supplies "
            "the regional reason."
        ),
    )

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None

    @field_validator("document_date", "due_date", "action_deadline")
    @classmethod
    def require_iso_dates(cls, value: str | None) -> str | None:
        if value is not None:
            Date.fromisoformat(value)
        return value


class FieldEvidence(BaseModel):
    """Page-specific direct or contextually inferred evidence."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    quote: str = Field(min_length=1, max_length=500)
    page_number: int = Field(ge=1)
    evidence_type: Literal["direct", "inferred"] = "direct"
    reason: str | None = Field(default=None, max_length=500)


class DocumentExtractionEvidence(BaseModel):
    """Evidence for fields that must be grounded in document text."""

    model_config = ConfigDict(extra="forbid")

    amount: FieldEvidence | None = None
    currency: FieldEvidence | None = None
    document_date: FieldEvidence | None = None
    due_date: FieldEvidence | None = None
    action_deadline: FieldEvidence | None = None
    sender: FieldEvidence | None = None
