import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.routes import router as api_router
from app.core.config import settings
from app.services.calendar_observability import calendar_beat_health
from app.web import FRONTEND_DIR, router as web_router
from app.web_backups import router as web_backups_router
from app.web_document_ai import router as web_document_ai_router


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    issues = settings.email_reminder_configuration_issues()
    if issues and settings.email_reminders_enabled:
        # Configuration names only: never expose SMTP credentials in logs.
        logger.warning("Email reminders are disabled: missing or unsafe configuration: %s", ", ".join(issues))
    yield

app = FastAPI(
    title="DocsFlow API",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Apply baseline browser protections to HTML and API responses."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    is_document_preview = (
        request.url.path.startswith("/api/v1/documents/")
        and request.url.path.endswith("/preview")
    )
    if is_document_preview:
        response.headers.setdefault(
            "Content-Security-Policy",
            "base-uri 'none'; frame-ancestors 'self'; object-src 'none'",
        )
    else:
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Content-Security-Policy",
            (
                "default-src 'self'; "
                "base-uri 'self'; "
                "form-action 'self'; "
                "frame-ancestors 'none'; "
                "object-src 'none'; "
                "img-src 'self' data:; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "script-src 'self' 'unsafe-inline'; "
                "connect-src 'self'"
            ),
        )
    return response


@app.get("/health", tags=["system"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/beat", tags=["system"])
def celery_beat_health_check() -> dict[str, object]:
    """Read the heartbeat written by the periodic calendar reminder task."""
    payload = calendar_beat_health()
    if payload["status"] != "ok":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=payload,
        )
    return payload


app.include_router(
    api_router,
    prefix="/api/v1",
)

app.mount(
    "/assets",
    StaticFiles(directory=FRONTEND_DIR / "assets"),
    name="frontend_assets",
)
app.include_router(web_router)
app.include_router(web_backups_router)
app.include_router(web_document_ai_router)
