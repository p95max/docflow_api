from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
            date.fromisoformat(value)
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
