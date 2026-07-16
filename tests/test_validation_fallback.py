import pytest

from app.core.config import settings
from app.tasks.documents import should_run_validation_fallback


def test_validation_fallback_requires_needs_review_and_another_configured_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "openai_validation_fallback_model", "gpt-4o")

    assert should_run_validation_fallback(
        validation_status="needs_review",
        primary_model="gpt-4o-mini",
    )
    assert not should_run_validation_fallback(
        validation_status="valid",
        primary_model="gpt-4o-mini",
    )
    assert not should_run_validation_fallback(
        validation_status="needs_review",
        primary_model="gpt-4o",
    )
