from pydantic import BaseModel

from app.core.config import settings
from app.schemas.ai_processing import DocumentAIExtraction
from app.services.extraction_sanitization import sanitize_ai_extraction
from app.services.openai_client import (
    OpenAIUsage,
    create_openai_client,
    extract_openai_usage,
)


SYSTEM_PROMPT = """
You are a document processing engine.

Extract structured data from the provided document text.
Return only data that is supported by the text.
Do not invent values.
If a field is missing or unclear, use null.
Dates must use ISO format YYYY-MM-DD when possible.
Currency must use ISO 4217 codes like EUR or USD.
"""


class StandardAIProcessingResult(BaseModel):
    model: str
    response_id: str | None
    extracted_data: DocumentAIExtraction
    usage: OpenAIUsage


def run_standard_ai_processing(
    *,
    raw_text: str,
    original_filename: str,
    model: str | None = None,
) -> StandardAIProcessingResult:
    """Run AI classification and structured extraction for standard documents."""
    normalized_text = raw_text.strip()

    if not normalized_text:
        raise ValueError("Cannot run AI processing because raw_text is empty.")

    client = create_openai_client()

    selected_model = model or settings.openai_model
    response = client.responses.parse(
        model=selected_model,
        input=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT.strip(),
            },
            {
                "role": "user",
                "content": _build_user_prompt(
                    raw_text=normalized_text,
                    original_filename=original_filename,
                ),
            },
        ],
        text_format=DocumentAIExtraction,
    )

    parsed = response.output_parsed

    if parsed is None:
        raise ValueError("OpenAI response did not contain parsed structured output.")

    extracted_data = sanitize_ai_extraction(
        extraction=DocumentAIExtraction.model_validate(parsed),
        raw_text=normalized_text,
    )

    return StandardAIProcessingResult(
        model=selected_model,
        response_id=getattr(response, "id", None),
        extracted_data=extracted_data,
        usage=extract_openai_usage(response),
    )


def _build_user_prompt(
    *,
    raw_text: str,
    original_filename: str,
) -> str:
    safe_text = raw_text[: settings.openai_max_input_chars]

    return f"""
Original filename:
{original_filename}

Document text:
{safe_text}
""".strip()
