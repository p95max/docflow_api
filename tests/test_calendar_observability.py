from datetime import UTC, datetime, timedelta
import logging

import pytest
from fastapi import HTTPException
from redis.exceptions import RedisError

import app.main as main
import app.services.calendar_observability as observability


class _FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, int]] = {}
        self.values: dict[str, str] = {}

    def hincrby(self, key: str, name: str, amount: int) -> int:
        values = self.hashes.setdefault(key, {})
        values[name] = values.get(name, 0) + amount
        return values[name]

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def get(self, key: str) -> str | None:
        return self.values.get(key)


class _UnavailableRedis:
    def hincrby(self, key: str, name: str, amount: int) -> int:
        raise RedisError("unavailable")

    def set(self, key: str, value: str) -> None:
        raise RedisError("unavailable")


def test_calendar_counters_and_beat_heartbeat_are_shared_without_document_content(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.services.calendar_observability")
    fake_redis = _FakeRedis()
    monkeypatch.setattr(observability, "_redis_client", lambda: fake_redis)

    observability.increment_calendar_counter("events_created")
    observability.increment_calendar_counter("reminders_sent", amount=2)
    observability.record_beat_heartbeat(now=datetime(2026, 7, 23, 8, tzinfo=UTC))
    health = observability.calendar_beat_health(
        now=datetime(2026, 7, 23, 8, 1, tzinfo=UTC)
    )
    observability.log_calendar_event(
        "calendar_projection_result",
        owner_id=1,
        document_id=2,
        created=1,
    )

    assert fake_redis.hashes["docsflow:calendar:metrics"] == {
        "events_created": 1,
        "reminders_sent": 2,
    }
    assert health == {"status": "ok", "age_seconds": 60}
    assert '"document_id": 2' in caplog.text
    assert "OCR" not in caplog.text


def test_calendar_observability_logs_safe_error_type_when_redis_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="app.services.calendar_observability")
    monkeypatch.setattr(observability, "_redis_client", lambda: _UnavailableRedis())

    observability.increment_calendar_counter("events_created")
    observability.record_beat_heartbeat(now=datetime(2026, 7, 23, 8, tzinfo=UTC))

    assert "metrics_unavailable counter=events_created error_type=RedisError" in caplog.text
    assert "beat_heartbeat_unavailable error_type=RedisError" in caplog.text
    assert "RedisError: unavailable" not in caplog.text


def test_beat_health_reports_stale_and_api_returns_service_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_redis = _FakeRedis()
    monkeypatch.setattr(observability, "_redis_client", lambda: fake_redis)
    stale_at = datetime.now(UTC) - timedelta(hours=1)
    fake_redis.set("docsflow:calendar:beat:last-run", stale_at.isoformat())

    stale = observability.calendar_beat_health(now=datetime.now(UTC))
    assert stale["status"] == "stale"

    monkeypatch.setattr(main, "calendar_beat_health", lambda: stale)
    with pytest.raises(HTTPException) as exc_info:
        main.celery_beat_health_check()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == stale
