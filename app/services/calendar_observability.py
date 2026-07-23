"""Safe operational signals for the calendar domain.

The functions in this module are deliberately fail-open: unavailable metrics
storage must never prevent a document, calendar event, or reminder from being
processed.  Log payloads contain identifiers and counts only, never OCR text
or extracted document values.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import settings


logger = logging.getLogger(__name__)

_METRICS_KEY = "docsflow:calendar:metrics"
_BEAT_HEARTBEAT_KEY = "docsflow:calendar:beat:last-run"


@lru_cache(maxsize=1)
def _redis_client() -> Redis | None:
    url = settings.celery_broker_url
    if not url.startswith(("redis://", "rediss://")):
        return None
    return Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=1,
    )


def log_calendar_event(event: str, **fields: object) -> None:
    """Write a machine-readable calendar log event without document contents."""
    payload = {"event": event, **fields}
    logger.info("calendar_observability=%s", json.dumps(payload, default=str, sort_keys=True))


def increment_calendar_counter(name: str, amount: int = 1) -> None:
    """Increment a shared Redis counter when Redis is configured and reachable."""
    if amount <= 0:
        return
    try:
        client = _redis_client()
        if client is not None:
            client.hincrby(_METRICS_KEY, name, amount)
    except (RedisError, OSError, ValueError):
        logger.warning("calendar_observability=metrics_unavailable counter=%s", name)


def record_beat_heartbeat(*, now: datetime | None = None) -> None:
    """Record the last successful reminder scheduler run for the API health probe."""
    timestamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
    try:
        client = _redis_client()
        if client is not None:
            client.set(_BEAT_HEARTBEAT_KEY, timestamp)
    except (RedisError, OSError, ValueError):
        logger.warning("calendar_observability=beat_heartbeat_unavailable")


def calendar_beat_health(*, now: datetime | None = None) -> dict[str, Any]:
    """Return Beat freshness without exposing broker credentials or internals."""
    current_time = (now or datetime.now(UTC)).astimezone(UTC)
    try:
        client = _redis_client()
        if client is None:
            return {"status": "unavailable", "reason": "Redis broker is not configured."}
        raw_timestamp = client.get(_BEAT_HEARTBEAT_KEY)
    except (RedisError, OSError, ValueError):
        return {"status": "unavailable", "reason": "Redis heartbeat is unavailable."}

    if not raw_timestamp:
        return {"status": "stale", "reason": "No Beat heartbeat has been recorded."}
    try:
        last_run = datetime.fromisoformat(raw_timestamp)
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=UTC)
        age_seconds = max(0, int((current_time - last_run.astimezone(UTC)).total_seconds()))
    except ValueError:
        return {"status": "unavailable", "reason": "Beat heartbeat is invalid."}

    if age_seconds > settings.calendar_beat_health_max_age_seconds:
        return {"status": "stale", "age_seconds": age_seconds}
    return {"status": "ok", "age_seconds": age_seconds}
