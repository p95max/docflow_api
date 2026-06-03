from typing import Literal

from pydantic import BaseModel, Field


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
    document_type: DocumentType = Field(
        description="Best matching document type."
    )
    summary: str | None = Field(
        description="Short human-readable summary of the document."
    )
    sender: str | None = Field(
        description="Sender, vendor, company, authority or person who issued the document."
    )
    recipient: str | None = Field(
        description="Recipient name, company or person if available."
    )
    document_date: str | None = Field(
        description="Document date in ISO format YYYY-MM-DD if available."
    )
    due_date: str | None = Field(
        description="Payment due date, deadline or response deadline in ISO format YYYY-MM-DD if available."
    )
    total_amount: float | None = Field(
        description="Total amount if the document contains one."
    )
    currency: str | None = Field(
        description="ISO currency code like EUR or USD if available."
    )
    invoice_number: str | None = Field(
        description="Invoice number if available."
    )
    reference_number: str | None = Field(
        description="Customer number, reference number, case number or similar identifier."
    )
    requires_action: bool = Field(
        description="Whether the document requires user action."
    )
    action_deadline: str | None = Field(
        description="Action deadline in ISO format YYYY-MM-DD if available."
    )
    confidence_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score between 0 and 1.",
    )
    notes: str | None = Field(
        description="Important caveats or missing information."
    )