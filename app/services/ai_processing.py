from typing import Any

from openai import OpenAI
from pydantic import BaseModel

from app.core.config import settings
from app.schemas.ai_processing import DocumentAIExtraction


SYSTEM_PROMPT = """
You are a document processing engine.

Extract structured data from the provided document text.
Return only data that is supported by the text.
Do not invent values.
If a field is missing or unclear, use null.
Dates must use ISO format YYYY-MM-DD when possible.
Currency must use ISO 4217 codes like EUR or USD.
"""


class OpenAIUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class StandardAIProcessingResult(BaseModel):
    model: str
    response_id: str | None
    extracted_data: DocumentAIExtraction
    usage: OpenAIUsage


def run_standard_ai_processing(
    *,
    raw_text: str,
    original_filename: str,
) -> StandardAIProcessingResult:
    """Run AI classification and structured extraction for standard documents."""
    normalized_text = raw_text.strip()

    if not normalized_text:
        raise ValueError("Cannot run AI processing because raw_text is empty.")

    if not settings.openai_api_key or not settings.openai_api_key.strip():
        raise RuntimeError("OPENAI_API_KEY is required for standard AI processing.")

    client = OpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_request_timeout_seconds,
    )

    response = client.responses.parse(
        model=settings.openai_model,
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

    extracted_data = DocumentAIExtraction.model_validate(parsed)

    return StandardAIProcessingResult(
        model=settings.openai_model,
        response_id=getattr(response, "id", None),
        extracted_data=extracted_data,
        usage=_extract_usage(response),
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


def _extract_usage(response: Any) -> OpenAIUsage:
    usage = getattr(response, "usage", None)

    input_tokens = _read_int_attribute(
        usage,
        "input_tokens",
        "prompt_tokens",
    )
    output_tokens = _read_int_attribute(
        usage,
        "output_tokens",
        "completion_tokens",
    )
    total_tokens = _read_int_attribute(
        usage,
        "total_tokens",
    )

    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    return OpenAIUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def _read_int_attribute(
    obj: Any,
    *attribute_names: str,
) -> int | None:
    if obj is None:
        return None

    for attribute_name in attribute_names:
        value = getattr(obj, attribute_name, None)

        if isinstance(value, int):
            return value

    return None