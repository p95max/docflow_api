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


def sanitize_ai_extraction(
    *,
    extraction: DocumentAIExtraction,
    raw_text: str,
) -> DocumentAIExtraction:
    """Remove deterministic sample metadata without mutating model output.

    Templates commonly contain realistic-looking sample people and addresses.
    They are valid text evidence but must not become document metadata.
    """
    if not is_document_template(raw_text):
        return extraction

    updates: dict[str, object] = {}
    evidence = extraction.evidence

    if extraction.sender and looks_like_template_example(extraction.sender):
        updates["sender"] = None
        updates["evidence"] = evidence.model_copy(update={"sender": None})

    if not updates:
        return extraction

    return extraction.model_copy(update=updates, deep=True)


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
