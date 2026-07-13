import unicodedata

from app.schemas.ai_processing import DocumentType


MIN_CLASSIFICATION_SCORE = 5

DOCUMENT_TYPE_RULES: dict[DocumentType, dict[str, int]] = {
    "invoice": {
        "invoice number": 5,
        "invoice": 4,
        "rechnungsnummer": 5,
        "rechnung": 4,
        "amount due": 3,
        "subtotal": 2,
        "vat": 2,
        "gst": 2,
        "total": 1,
    },
    "receipt": {
        "receipt": 5,
        "kassenbon": 5,
        "quittung": 5,
        "cash": 2,
        "change": 2,
        "thank you": 1,
    },
    "letter": {
        "sehr geehrte": 4,
        "mit freundlichen grüßen": 4,
        "mit freundlichen gruessen": 4,
        "dear": 2,
        "sincerely": 2,
        "betreff": 2,
        "subject": 1,
    },
    "contract": {
        "contract": 5,
        "vertrag": 5,
        "agreement": 4,
        "vereinbarung": 4,
        "terms and conditions": 3,
        "unterschrift": 2,
    },
    "bank_statement": {
        "account statement": 5,
        "kontoauszug": 5,
        "iban": 2,
        "bic": 2,
        "balance": 2,
        "saldo": 2,
        "transaction": 1,
        "buchung": 1,
    },
    "tax_document": {
        "tax return": 5,
        "steuerbescheid": 5,
        "steuererklärung": 5,
        "steuererklaerung": 5,
        "finanzamt": 4,
        "tax office": 4,
        "einkommensteuer": 4,
    },
    "medical_document": {
        "medical report": 5,
        "befund": 5,
        "diagnosis": 4,
        "diagnose": 4,
        "prescription": 3,
        "rezept": 3,
        "patient": 2,
        "arzt": 2,
        "krankenkasse": 2,
    },
    "other": {},
}


def classify_document_type(raw_text: str) -> DocumentType:
    """Classify extracted text locally without sending document data externally."""
    normalized_text = _normalize_text(raw_text)

    if not normalized_text:
        return "other"

    scores = {
        document_type: sum(
            weight
            for phrase, weight in rules.items()
            if phrase in normalized_text
        )
        for document_type, rules in DOCUMENT_TYPE_RULES.items()
        if document_type != "other"
    }

    ranked_types = sorted(
        scores.items(),
        key=lambda item: item[1],
        reverse=True,
    )

    winner, winner_score = ranked_types[0]

    if winner_score < MIN_CLASSIFICATION_SCORE:
        return "other"

    if len(ranked_types) > 1 and ranked_types[1][1] == winner_score:
        return "other"

    return winner


def _normalize_text(value: str) -> str:
    normalized_value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized_value.split())
