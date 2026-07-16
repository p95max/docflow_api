"""Deterministic sanitization of AI extraction results before validation."""

from __future__ import annotations

import re

from app.schemas.ai_processing import DocumentAIExtraction

_TEMPLATE_PLACEHOLDER_PATTERN = re.compile(r"\[[^\]\n]{2,80}\]")
_TEMPLATE_EXAMPLE_VALUE_PATTERN = re.compile(
    r"\b(?:muster(?:mann|frau|stadt|weg|straße|strasse)?|"
    r"beispiel(?:name|stadt|straße|strasse|weg)?)\b",
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
_INVOICE_SUBJECT_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\borganic items?\b", re.IGNORECASE), "organic products"),
)


def sanitize_ai_extraction(
    *,
    extraction: DocumentAIExtraction,
    raw_text: str,
) -> DocumentAIExtraction:
    """Sanitize deterministic sample data and fill safe derived metadata.

    The function never mutates the model output. Existing AI summaries are kept;
    a deterministic fallback is generated only when summary is missing or blank.
    """
    updates: dict[str, object] = {}
    evidence = extraction.evidence

    if is_document_template(raw_text):
        if extraction.sender and looks_like_template_example(extraction.sender):
            updates["sender"] = None
            updates["evidence"] = evidence.model_copy(update={"sender": None})

    effective_sender = updates.get("sender", extraction.sender)
    if not (extraction.summary or "").strip():
        fallback_summary = build_deterministic_summary(
            extraction=extraction,
            raw_text=raw_text,
            sender=effective_sender if isinstance(effective_sender, str) else None,
        )
        if fallback_summary:
            updates["summary"] = fallback_summary

    if not updates:
        return extraction

    return extraction.model_copy(update=updates, deep=True)


def build_deterministic_summary(
    *,
    extraction: DocumentAIExtraction,
    raw_text: str,
    sender: str | None = None,
) -> str | None:
    """Build a conservative summary from already extracted, bounded fields."""
    if extraction.document_type != "invoice":
        return None

    parts = ["Invoice"]
    if sender:
        parts.append(f"from {sender}")

    subject = _infer_invoice_subject(raw_text)
    if subject:
        parts.append(f"for {subject}")

    if extraction.total_amount is not None and extraction.currency:
        amount = f"{extraction.total_amount:.2f}"
        parts.append(f"totaling {amount} {extraction.currency}")

    return " ".join(parts) + "."


def _infer_invoice_subject(raw_text: str) -> str | None:
    for pattern, subject in _INVOICE_SUBJECT_RULES:
        if pattern.search(raw_text):
            return subject
    return None


def is_document_template(raw_text: str) -> bool:
    """Detect instructional/sample documents using explicit local markers."""
    source = raw_text.strip()
    normalized = source.casefold()
    if "musterbrief" in normalized:
        return True

    marker_count = sum(marker in normalized for marker in _TEMPLATE_MARKERS)
    placeholder_count = len(_TEMPLATE_PLACEHOLDER_PATTERN.findall(source))
    return marker_count >= 2 or (marker_count >= 1 and placeholder_count >= 2)


def looks_like_template_example(value: str) -> bool:
    """Return whether a field contains conventional sample-data markers."""
    return _TEMPLATE_EXAMPLE_VALUE_PATTERN.search(value) is not None
