import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session
from starlette.requests import Request

import app.services.rate_limits as rate_limits
from app.core.config import settings
from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.openai_usage_log import OpenAIUsageLog
from app.models.user import User
from app.models.calendar_event import CalendarEventType
from app.schemas.calendar_event import CalendarEventCreate
from app.services.calendar_events import create_user_event


class FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def eval(
        self,
        script: str,
        key_count: int,
        key: str,
        window_seconds: int,
    ) -> list[int]:
        assert script
        assert key_count == 1
        self.counts[key] = self.counts.get(key, 0) + 1
        return [self.counts[key], window_seconds]


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [],
            "client": ("203.0.113.10", 12345),
        }
    )


def _enable_fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    fake_redis = FakeRedis()
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(rate_limits, "_redis_client", lambda: fake_redis)
    return fake_redis


def test_login_limit_is_shared_by_ip_and_normalized_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_fake_redis(monkeypatch)
    monkeypatch.setattr(settings, "login_rate_limit_requests", 1)

    rate_limits.enforce_login_rate_limit(
        request=_request(),
        email="User@Example.com",
    )
    with pytest.raises(HTTPException) as exc_info:
        rate_limits.enforce_login_rate_limit(
            request=_request(),
            email="user@example.com",
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers == {"Retry-After": "300"}


def test_question_limit_is_per_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_fake_redis(monkeypatch)
    monkeypatch.setattr(settings, "knowledge_question_rate_limit_requests", 1)

    rate_limits.enforce_knowledge_question_rate_limit(user_id=7)
    with pytest.raises(HTTPException) as exc_info:
        rate_limits.enforce_knowledge_question_rate_limit(user_id=7)

    assert exc_info.value.status_code == 429


def test_calendar_writes_are_rate_limited_for_api_and_html_service_paths(
    db_session: Session,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_fake_redis(monkeypatch)
    monkeypatch.setattr(settings, "calendar_write_rate_limit_requests", 1)
    payload = CalendarEventCreate(
        title="First event",
        event_type=CalendarEventType.custom,
        start_date="2026-08-01",
    )
    create_user_event(db=db_session, owner_id=test_user.id, payload=payload)

    with pytest.raises(HTTPException) as exc_info:
        create_user_event(
            db=db_session,
            owner_id=test_user.id,
            payload=payload.model_copy(update={"title": "Second event"}),
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers == {"Retry-After": "60"}


def test_openai_token_quota_counts_legacy_document_usage(
    db_session: Session,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_fake_redis(monkeypatch)
    monkeypatch.setattr(settings, "openai_daily_token_quota", 100)
    document = Document(
        owner_id=test_user.id,
        original_filename="legacy-usage.pdf",
        status=DocumentStatus.completed,
        processing_mode=ProcessingMode.standard,
    )
    db_session.add(document)
    db_session.flush()
    db_session.add(
        OpenAIUsageLog(
            document_id=document.id,
            owner_id=None,
            operation="document_ai_extraction",
            model="test-model",
            total_tokens=100,
        )
    )
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        rate_limits.enforce_openai_usage_quota(
            db=db_session,
            owner_id=test_user.id,
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "Daily OpenAI token quota exceeded."


def test_rate_limits_fail_closed_when_redis_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenRedis:
        def eval(self, *_: object) -> None:
            raise OSError("redis unavailable")

    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(rate_limits, "_redis_client", lambda: BrokenRedis())

    with pytest.raises(HTTPException) as exc_info:
        rate_limits.enforce_knowledge_question_rate_limit(user_id=9)

    assert exc_info.value.status_code == 503
