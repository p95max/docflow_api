import hashlib
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from fastapi import HTTPException, Request, status
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.document import Document
from app.models.openai_usage_log import OpenAIUsageLog


_INCREMENT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
return {current, ttl}
"""


@lru_cache(maxsize=1)
def _redis_client() -> Redis:
    return Redis.from_url(
        settings.rate_limit_redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


def enforce_rate_limit(
    *,
    scope: str,
    identity: str,
    requests: int,
    window_seconds: int,
    detail: str,
) -> None:
    """Increment a shared fixed-window counter and fail closed if Redis is down."""
    if not settings.rate_limit_enabled:
        return

    identity_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    key = f"docsflow:rate-limit:{scope}:{identity_hash}"
    try:
        result = _redis_client().eval(
            _INCREMENT_SCRIPT,
            1,
            key,
            window_seconds,
        )
        count, ttl = int(result[0]), max(int(result[1]), 1)
    except (RedisError, OSError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Rate limit service is temporarily unavailable.",
        ) from exc

    if count > requests:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=detail,
            headers={"Retry-After": str(ttl)},
        )


def enforce_login_rate_limit(*, request: Request, email: str) -> None:
    _enforce_auth_limits(
        action="login",
        request=request,
        principal=email,
        requests=settings.login_rate_limit_requests,
        window_seconds=settings.login_rate_limit_window_seconds,
        detail="Login rate limit exceeded. Please try again later.",
    )


def enforce_registration_rate_limit(*, request: Request, email: str) -> None:
    _enforce_auth_limits(
        action="registration",
        request=request,
        principal=email,
        requests=settings.registration_rate_limit_requests,
        window_seconds=settings.registration_rate_limit_window_seconds,
        detail="Registration rate limit exceeded. Please try again later.",
    )


def enforce_upload_rate_limit(*, user_id: int) -> None:
    enforce_rate_limit(
        scope="upload-user",
        identity=str(user_id),
        requests=settings.upload_rate_limit_requests,
        window_seconds=settings.upload_rate_limit_window_seconds,
        detail="Upload rate limit exceeded. Please try again later.",
    )


def enforce_semantic_search_rate_limit(*, user_id: int) -> None:
    enforce_rate_limit(
        scope="semantic-search-user",
        identity=str(user_id),
        requests=settings.semantic_search_rate_limit_requests,
        window_seconds=settings.semantic_search_rate_limit_window_seconds,
        detail="Semantic search rate limit exceeded. Please try again later.",
    )


def enforce_knowledge_question_rate_limit(*, user_id: int) -> None:
    enforce_rate_limit(
        scope="knowledge-question-user",
        identity=str(user_id),
        requests=settings.knowledge_question_rate_limit_requests,
        window_seconds=settings.knowledge_question_rate_limit_window_seconds,
        detail="Question rate limit exceeded. Please try again later.",
    )


def enforce_openai_usage_quota(*, db: Session, owner_id: int) -> None:
    """Bound per-user OpenAI requests and already-recorded token usage."""
    if not settings.rate_limit_enabled:
        return

    total_tokens = db.scalar(
        select(
            func.coalesce(
                func.sum(
                    func.coalesce(
                        OpenAIUsageLog.total_tokens,
                        func.coalesce(OpenAIUsageLog.input_tokens, 0)
                        + func.coalesce(OpenAIUsageLog.output_tokens, 0),
                    )
                ),
                0,
            )
        )
        .select_from(OpenAIUsageLog)
        .outerjoin(Document, Document.id == OpenAIUsageLog.document_id)
        .where(
            OpenAIUsageLog.created_at >= datetime.now(UTC) - timedelta(days=1),
            or_(
                OpenAIUsageLog.owner_id == owner_id,
                and_(
                    OpenAIUsageLog.owner_id.is_(None),
                    Document.owner_id == owner_id,
                ),
            ),
        )
    ) or 0
    if int(total_tokens) >= settings.openai_daily_token_quota:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Daily OpenAI token quota exceeded.",
            headers={"Retry-After": "3600"},
        )

    enforce_rate_limit(
        scope="openai-daily-user",
        identity=str(owner_id),
        requests=settings.openai_daily_request_quota,
        window_seconds=24 * 60 * 60,
        detail="Daily OpenAI request quota exceeded.",
    )


def _enforce_auth_limits(
    *,
    action: str,
    request: Request,
    principal: str,
    requests: int,
    window_seconds: int,
    detail: str,
) -> None:
    client_host = request.client.host if request.client else "unknown"
    enforce_rate_limit(
        scope=f"{action}-ip",
        identity=client_host,
        # Keep the IP guard broad enough for offices/NAT while the normalized
        # account key remains strict against targeted credential attacks.
        requests=requests * 10,
        window_seconds=window_seconds,
        detail=detail,
    )
    enforce_rate_limit(
        scope=f"{action}-principal",
        identity=principal.strip().casefold(),
        requests=requests,
        window_seconds=window_seconds,
        detail=detail,
    )
