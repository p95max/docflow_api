from typing import Any

from openai import OpenAI
from pydantic import BaseModel

from app.core.config import settings


class OpenAIUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


def create_openai_client() -> OpenAI:
    """Create the shared synchronous client used by all external AI operations."""
    if not settings.openai_api_key or not settings.openai_api_key.strip():
        raise RuntimeError("OPENAI_API_KEY is required for OpenAI operations.")

    return OpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_request_timeout_seconds,
    )


def extract_openai_usage(response: Any) -> OpenAIUsage:
    usage = getattr(response, "usage", None)
    input_tokens = _read_int_attribute(usage, "input_tokens", "prompt_tokens")
    output_tokens = _read_int_attribute(
        usage,
        "output_tokens",
        "completion_tokens",
    )
    total_tokens = _read_int_attribute(usage, "total_tokens")

    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    return OpenAIUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def _read_int_attribute(obj: Any, *attribute_names: str) -> int | None:
    if obj is None:
        return None

    for attribute_name in attribute_names:
        value = getattr(obj, attribute_name, None)
        if isinstance(value, int):
            return value

    return None
