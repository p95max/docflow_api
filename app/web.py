from calendar import monthcalendar
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import secrets
from urllib.parse import urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import jwt
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from jinja2.runtime import Context
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, object_session, selectinload

from app.api.v1.routes_document_deletion import (
    delete_document as api_delete_document,
)
from app.api.v1.routes_documents import (
    confirm_document_extraction as api_confirm_document_extraction,
    correct_document_result as api_correct_document_result,
    get_document_result as api_get_document_result,
    get_my_document as api_get_my_document,
    list_my_documents as api_list_my_documents,
    reprocess_document as api_reprocess_document,
    update_document_note as api_update_document_note,
    upload_document as api_upload_document,
)
from app.core.config import settings
from app.core.timezones import DEFAULT_USER_TIMEZONE, validate_iana_timezone
from app.db.session import get_db
from app.models.user import User
from app.models.calendar_event import (
    CalendarEvent,
    CalendarEventSource,
    CalendarEventStatus,
    CalendarEventType,
)
from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.document_index_job import DocumentIndexJob, DocumentIndexJobStatus
from app.models.knowledge_message import KnowledgeMessageRole
from app.schemas.document import DocumentCorrection, DocumentNoteUpdate
from app.schemas.calendar_event import (
    CalendarEventCreate,
    CalendarEventUpdate,
    CalendarRangeQuery,
)
from app.schemas.knowledge import KnowledgeConversationCreate, KnowledgeQuestionCreate
from app.services.document_search import SortDirection, normalize_document_sort_field
from app.services import calendar_events
from app.services.document_index_jobs import enqueue_document_index_job
from app.services.knowledge_conversations import (
    answer_conversation_question,
    create_conversation,
    delete_conversation,
    get_owned_conversation,
    list_conversations,
)
from app.models.event_reminder import EventReminder, EventReminderChannel, EventReminderStatus
from app.services.calendar_feeds import (
    generate_calendar_feed_token,
    revoke_calendar_feed_token,
)
from app.services.reminder_scheduler import (
    configure_email_reminders,
    configure_in_app_reminders,
    disable_email_reminders_for_owner,
)
from app.services.icalendar import ICAL_CONTENT_TYPE, serialize_event_calendar
from app.services.notifications import (
    NotificationNotFoundError,
    list_notifications,
    mark_all_notifications_as_read,
    mark_notification_as_read,
    unread_notification_count,
)
from app.schemas.user import UserCreate, UserTimezoneUpdate
from app.services.security import create_access_token, decode_access_token
from app.services.rate_limits import (
    enforce_knowledge_question_rate_limit,
    enforce_login_rate_limit,
    enforce_registration_rate_limit,
)
from app.services.uploads import enforce_upload_rate_limit
from app.services.users import (
    authenticate_user,
    create_user,
    get_user_by_email,
    get_user_by_id,
    update_user_timezone,
)


FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"
TEMPLATES_DIR = FRONTEND_DIR / "templates"
SESSION_COOKIE_NAME = "docsflow_access_token"
CSRF_COOKIE_NAME = "docsflow_csrf_token"
DOCUMENT_TYPES = (
    "invoice",
    "receipt",
    "letter",
    "contract",
    "bank_statement",
    "tax_document",
    "medical_document",
    "other",
)
router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def _format_file_size(value: int | None) -> str:
    if value is None:
        return "—"
    return f"{value / (1024 * 1024):.2f} MB"


def _status_badge_class(value: object) -> str:
    status_value = getattr(value, "value", str(value))
    return {
        "uploaded": "text-bg-secondary",
        "processing": "text-bg-info",
        "completed": "text-bg-success",
        "failed": "text-bg-danger",
        "warning": "text-bg-warning",
        "needs_review": "text-bg-warning",
        "valid": "text-bg-success",
        "draft": "text-bg-secondary",
        "corrected": "text-bg-warning",
        "confirmed": "text-bg-success",
        "suggested": "text-bg-info",
        "cancelled": "text-bg-secondary",
    }.get(status_value, "text-bg-secondary")


def _get_user_timezone_name(user: User | None) -> str:
    if user is None:
        return DEFAULT_USER_TIMEZONE
    try:
        return validate_iana_timezone(user.timezone)
    except ValueError:
        return DEFAULT_USER_TIMEZONE


def _to_user_timezone(value: datetime, timezone_name: str) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo(timezone_name))


def _format_user_datetime(value: datetime, timezone_name: str) -> str:
    return _to_user_timezone(value, timezone_name).strftime("%H:%M %d-%m-%Y")


def _format_user_date(value: datetime, timezone_name: str) -> str:
    return _to_user_timezone(value, timezone_name).strftime("%d-%m-%Y")


def _format_user_time(value: datetime, timezone_name: str) -> str:
    return _to_user_timezone(value, timezone_name).strftime("%H:%M")


@pass_context
def _format_local_datetime(context: Context, value: datetime) -> str:
    timezone_name = str(context.get("display_timezone", DEFAULT_USER_TIMEZONE))
    return _format_user_datetime(value, timezone_name)


@pass_context
def _format_local_date(context: Context, value: datetime) -> str:
    timezone_name = str(context.get("display_timezone", DEFAULT_USER_TIMEZONE))
    return _format_user_date(value, timezone_name)


@pass_context
def _format_local_time(context: Context, value: datetime) -> str:
    timezone_name = str(context.get("display_timezone", DEFAULT_USER_TIMEZONE))
    return _format_user_time(value, timezone_name)


def _format_token_count(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def _get_ai_usage_summary(current_user: User | None) -> dict[str, object] | None:
    """Build a small, owner-only usage view for the navigation dialog."""
    if current_user is None:
        return None

    window_start = datetime.now(timezone.utc) - timedelta(days=1)
    user_timezone = ZoneInfo(_get_user_timezone_name(current_user))
    now_in_user_timezone = datetime.now(user_timezone)
    month_start = now_in_user_timezone.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    ).astimezone(timezone.utc)

    def summarize_since(start: datetime) -> dict[str, object]:
        usage_by_model: dict[str, dict[str, int]] = {}
        recorded_operations = 0
        used_tokens = 0

        for usage_log in current_user.openai_usage_logs:
            created_at = usage_log.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if created_at < start:
                continue

            token_count = usage_log.total_tokens
            if token_count is None:
                token_count = (usage_log.input_tokens or 0) + (usage_log.output_tokens or 0)

            used_tokens += token_count
            recorded_operations += 1
            model_usage = usage_by_model.setdefault(
                usage_log.model,
                {"tokens": 0, "operations": 0},
            )
            model_usage["tokens"] += token_count
            model_usage["operations"] += 1

        models = [
            {
                "name": model,
                "tokens": values["tokens"],
                "operations": values["operations"],
            }
            for model, values in usage_by_model.items()
        ]
        models.sort(key=lambda model: (-int(model["tokens"]), str(model["name"])))
        return {
            "used_tokens": used_tokens,
            "recorded_operations": recorded_operations,
            "models": models,
        }

    daily_usage = summarize_since(window_start)
    monthly_usage = summarize_since(month_start)
    used_tokens = int(daily_usage["used_tokens"])
    models = daily_usage["models"]

    limit_tokens = settings.openai_daily_token_quota
    return {
        "used_tokens": used_tokens,
        "limit_tokens": limit_tokens,
        "remaining_tokens": max(limit_tokens - used_tokens, 0),
        "percent_used": min((used_tokens / limit_tokens) * 100, 100),
        "recorded_operations": daily_usage["recorded_operations"],
        "models": models,
        "nav_model": str(models[0]["name"]) if models else settings.openai_rag_model,
        "monthly": {
            **monthly_usage,
            "label": now_in_user_timezone.strftime("%B %Y"),
        },
        "configured_models": (
            ("Document extraction", settings.openai_model),
            ("Ask Documents", settings.openai_rag_model),
            ("Embeddings", settings.openai_embedding_model),
        ),
    }


templates.env.filters["file_size"] = _format_file_size
templates.env.filters["status_badge_class"] = _status_badge_class
templates.env.filters["local_datetime"] = _format_local_datetime
templates.env.filters["local_date"] = _format_local_date
templates.env.filters["local_time"] = _format_local_time
templates.env.filters["token_count"] = _format_token_count


def _pagination_query(request: Request) -> str:
    params = [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key != "page"
    ]
    return urlencode(params)


def _document_sort_query(request: Request) -> str:
    """Keep active document filters while a header click changes the sorting."""
    params = [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key not in {"page", "sort_by", "sort_direction"}
    ]
    return urlencode(params)


def _get_web_current_user(
    request: Request,
    db: Session,
) -> User | None:
    token = request.cookies.get(SESSION_COOKIE_NAME)

    if not token:
        return None

    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        return None

    user = get_user_by_id(db, user_id)

    if user is None or not user.is_active:
        return None

    return user


def _redirect_to_login() -> RedirectResponse:
    return RedirectResponse(
        url="/login",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _is_local_development_host(request: Request) -> bool:
    hostname = request.url.hostname or ""
    return hostname in {"localhost", "127.0.0.1", "0.0.0.0"}


def _template_response(
    *,
    request: Request,
    name: str,
    current_user: User | None = None,
    status_code: int = status.HTTP_200_OK,
    **context: object,
) -> HTMLResponse:
    csrf_token = request.cookies.get(CSRF_COOKIE_NAME) or secrets.token_urlsafe(32)
    user_session = object_session(current_user) if current_user is not None else None
    notification_count = (
        unread_notification_count(db=user_session, owner_id=current_user.id)
        if user_session is not None and current_user is not None
        else 0
    )
    response = templates.TemplateResponse(
        request=request,
        name=name,
        context={
            "request": request,
            "current_user": current_user,
            "document_types": DOCUMENT_TYPES,
            "knowledge_enabled": settings.knowledge_enabled,
            "display_timezone": _get_user_timezone_name(current_user),
            "csrf_token": csrf_token,
            "ai_usage": _get_ai_usage_summary(current_user),
            "unread_notification_count": notification_count,
            **context,
        },
        status_code=status_code,
    )
    if request.cookies.get(CSRF_COOKIE_NAME) != csrf_token:
        response.set_cookie(
            key=CSRF_COOKIE_NAME,
            value=csrf_token,
            max_age=settings.access_token_expire_minutes * 60,
            secure=request.url.scheme == "https",
            samesite="strict",
            path="/",
        )
    return response


async def require_csrf(request: Request) -> None:
    """Require the double-submit CSRF token for cookie-authenticated HTML forms."""
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    form = await request.form()
    submitted_value = form.get("csrf_token")
    submitted_form_token = submitted_value if isinstance(submitted_value, str) else None
    submitted_tokens = (
        request.headers.get("X-CSRF-Token"),
        submitted_form_token,
    )
    if not cookie_token or not any(
        token and secrets.compare_digest(cookie_token, token)
        for token in submitted_tokens
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid CSRF token.",
        )


def _exception_message(exc: HTTPException) -> str:
    if isinstance(exc.detail, dict):
        return str(exc.detail.get("message") or exc.detail)
    return str(exc.detail)


@router.get("/notifications", response_class=HTMLResponse, response_model=None)
def notifications_page(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()
    return _template_response(
        request=request,
        name="notifications.html",
        current_user=current_user,
        notifications=list_notifications(db=db, owner_id=current_user.id),
    )


@router.post("/notifications/{notification_id}/read", response_model=None)
async def read_notification(
    notification_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()
    try:
        mark_notification_as_read(
            db=db,
            owner_id=current_user.id,
            notification_id=notification_id,
        )
    except NotificationNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found.")
    return RedirectResponse(url="/notifications", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/notifications/read-all", response_model=None)
async def read_all_notifications(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()
    mark_all_notifications_as_read(db=db, owner_id=current_user.id)
    return RedirectResponse(url="/notifications", status_code=status.HTTP_303_SEE_OTHER)


def _relative_url(value: str | None) -> str | None:
    if not value:
        return None

    parts = urlsplit(value)
    return urlunsplit(("", "", parts.path, parts.query, parts.fragment))


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None

    stripped = value.strip()
    return stripped or None


_REMINDER_SETTING_OFFSETS = {"0": 0, "60": 60, "1440": 24 * 60}


def _parse_reminder_settings(values: list[str]) -> set[int]:
    try:
        return {_REMINDER_SETTING_OFFSETS[value] for value in values}
    except KeyError as exc:
        raise ValueError("Choose a valid reminder setting.") from exc


def _render_document_detail(
    *,
    request: Request,
    db: Session,
    current_user: User,
    document_id: int,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    try:
        document = api_get_document_result(
            document_id=document_id,
            request=request,
            db=db,
            current_user=current_user,
        )
    except HTTPException as exc:
        return _template_response(
            request=request,
            name="error.html",
            current_user=current_user,
            error=_exception_message(exc),
            status_code=exc.status_code,
        )

    return _template_response(
        request=request,
        name="document_detail.html",
        current_user=current_user,
        document=document,
        preview_url=_relative_url(document.file_preview_url),
        download_url=_relative_url(document.file_download_url),
        recovery_data_only=(
            document.file_preview_url is None
            and document.file_size_bytes is None
        ),
        error=error,
        related_calendar_events=calendar_events.list_events(
            db=db,
            owner_id=current_user.id,
            user_timezone=_get_user_timezone_name(current_user),
            query=CalendarRangeQuery(document_id=document_id),
        ),
        status_code=status_code,
    )


def _list_knowledge_documents(
    *,
    db: Session,
    owner_id: int,
) -> list[Document]:
    return list(
        db.scalars(
            select(Document)
            .options(selectinload(Document.index_job))
            .where(
                Document.owner_id == owner_id,
                Document.deleted_at.is_(None),
            )
            .order_by(Document.created_at.desc(), Document.id.desc())
        ).all()
    )


def _list_selectable_knowledge_documents(
    *,
    db: Session,
    owner_id: int,
) -> list[Document]:
    """Return only documents that can provide RAG context."""
    return list(
        db.scalars(
            select(Document)
            .join(DocumentIndexJob, DocumentIndexJob.document_id == Document.id)
            .where(
                Document.owner_id == owner_id,
                Document.deleted_at.is_(None),
                Document.status == DocumentStatus.completed,
                Document.processing_mode == ProcessingMode.standard,
                Document.raw_text.is_not(None),
                DocumentIndexJob.status == DocumentIndexJobStatus.completed,
            )
            .order_by(Document.original_filename.asc(), Document.id.asc())
        ).all()
    )


def _render_knowledge_page(
    *,
    request: Request,
    db: Session,
    current_user: User,
    title_value: str = "",
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    return _template_response(
        request=request,
        name="knowledge.html",
        current_user=current_user,
        conversations=list_conversations(db=db, owner_id=current_user.id),
        documents=_list_knowledge_documents(db=db, owner_id=current_user.id),
        title_value=title_value,
        reindexed=request.query_params.get("reindexed") == "1",
        error=error,
        status_code=status_code,
    )


def _knowledge_disabled_response(
    *,
    request: Request,
    current_user: User,
) -> HTMLResponse:
    return _template_response(
        request=request,
        name="error.html",
        current_user=current_user,
        error="Knowledge Base is disabled by configuration.",
        status_code=status.HTTP_404_NOT_FOUND,
    )


def _render_knowledge_conversation(
    *,
    request: Request,
    db: Session,
    current_user: User,
    conversation_id: int,
    question_value: str = "",
    selected_document_id: int | None = None,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    try:
        conversation = get_owned_conversation(
            db=db,
            owner_id=current_user.id,
            conversation_id=conversation_id,
        )
    except LookupError as exc:
        return _template_response(
            request=request,
            name="error.html",
            current_user=current_user,
            error=str(exc),
            status_code=status.HTTP_404_NOT_FOUND,
        )

    last_read_message_id = conversation.last_read_assistant_message_id or 0
    unread_assistant_message_ids = {
        message.id
        for message in conversation.messages
        if message.role == KnowledgeMessageRole.assistant
        and message.id > last_read_message_id
    }
    latest_unread_assistant_message_id = max(
        unread_assistant_message_ids,
        default=None,
    )
    if latest_unread_assistant_message_id is not None:
        conversation.last_read_assistant_message_id = latest_unread_assistant_message_id
        db.commit()

    active_document_ids = set(
        db.scalars(
            select(Document.id).where(
                Document.owner_id == current_user.id,
                Document.deleted_at.is_(None),
            )
        ).all()
    )
    return _template_response(
        request=request,
        name="knowledge_conversation.html",
        current_user=current_user,
        conversation=conversation,
        active_document_ids=active_document_ids,
        unread_assistant_message_ids=unread_assistant_message_ids,
        latest_unread_assistant_message_id=latest_unread_assistant_message_id,
        question_value=question_value,
        selected_document_id=selected_document_id,
        selectable_documents=_list_selectable_knowledge_documents(
            db=db,
            owner_id=current_user.id,
        ),
        error=error,
        status_code=status_code,
    )


@router.get("/")
def frontend_root(
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    destination = (
        "/documents"
        if _get_web_current_user(request, db) is not None
        else "/login"
    )
    return RedirectResponse(
        url=destination,
        status_code=status.HTTP_302_FOUND,
    )


@router.get(
    "/login",
    response_class=HTMLResponse,
    response_model=None,
)
def login_page(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    if _get_web_current_user(request, db) is not None:
        return RedirectResponse(
            url="/documents",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    use_test_credentials = (
        settings.app_env == "local"
        and settings.init_test_user
        and _is_local_development_host(request)
    )

    return _template_response(
        request=request,
        name="login.html",
        email_value="m@m.com" if use_test_credentials else "",
        password_value="12345678" if use_test_credentials else "",
        remember_me=False,
        registered=request.query_params.get("registered") == "1",
    )


@router.post(
    "/login",
    response_class=HTMLResponse,
    response_model=None,
    dependencies=[Depends(require_csrf)],
)
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    remember_me: bool = Form(False),
    db: Session = Depends(get_db),
) -> Response:
    try:
        enforce_login_rate_limit(request=request, email=email)
    except HTTPException as exc:
        return _template_response(
            request=request,
            name="login.html",
            email_value=email,
            password_value="",
            remember_me=remember_me,
            error=_exception_message(exc),
            status_code=exc.status_code,
        )

    user = authenticate_user(
        db=db,
        email=email,
        password=password,
    )

    if user is None:
        return _template_response(
            request=request,
            name="login.html",
            email_value=email,
            password_value="",
            remember_me=remember_me,
            error="Incorrect email or password.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    session_minutes = settings.access_token_expire_minutes
    if remember_me:
        session_minutes = settings.remember_me_token_expire_days * 24 * 60

    token = create_access_token(
        subject=str(user.id),
        expires_in_minutes=session_minutes,
    )
    response = RedirectResponse(
        url="/documents",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=session_minutes * 60,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path="/",
    )
    return response


@router.post("/logout", dependencies=[Depends(require_csrf)])
def logout() -> RedirectResponse:
    response = RedirectResponse(
        url="/login",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
    )
    return response


@router.get(
    "/register",
    response_class=HTMLResponse,
    response_model=None,
)
def register_page(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    if _get_web_current_user(request, db) is not None:
        return RedirectResponse(
            url="/documents",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return _template_response(
        request=request,
        name="register.html",
        email_value="",
    )


@router.post(
    "/register",
    response_class=HTMLResponse,
    response_model=None,
    dependencies=[Depends(require_csrf)],
)
def register_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> Response:
    try:
        enforce_registration_rate_limit(request=request, email=email)
    except HTTPException as exc:
        return _template_response(
            request=request,
            name="register.html",
            email_value=email,
            error=_exception_message(exc),
            status_code=exc.status_code,
        )

    try:
        payload = UserCreate(
            email=email,
            password=password,
        )
    except ValidationError as exc:
        error = exc.errors()[0].get(
            "msg",
            "Invalid registration data.",
        )
        return _template_response(
            request=request,
            name="register.html",
            email_value=email,
            error=error,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    if get_user_by_email(db, str(payload.email)) is not None:
        return _template_response(
            request=request,
            name="register.html",
            email_value=email,
            error="Email already registered.",
            status_code=status.HTTP_409_CONFLICT,
        )

    try:
        create_user(
            db=db,
            email=str(payload.email),
            password=payload.password,
        )
    except IntegrityError:
        db.rollback()
        return _template_response(
            request=request,
            name="register.html",
            email_value=email,
            error="Email already registered.",
            status_code=status.HTTP_409_CONFLICT,
        )

    return RedirectResponse(
        url="/login?registered=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get(
    "/settings",
    response_class=HTMLResponse,
    response_model=None,
)
def settings_page(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()

    return _template_response(
        request=request,
        name="settings.html",
        current_user=current_user,
        timezone_value=current_user.timezone,
        updated=request.query_params.get("updated") == "1",
        email_reminders_updated=request.query_params.get("email_reminders_updated") == "1",
        calendar_feed_configured=current_user.calendar_feed_token_hash is not None,
    )


@router.post(
    "/settings/timezone",
    response_class=HTMLResponse,
    response_model=None,
    dependencies=[Depends(require_csrf)],
)
def update_timezone_setting(
    request: Request,
    timezone: str = Form(...),
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()

    try:
        payload = UserTimezoneUpdate(timezone=timezone)
    except ValidationError as exc:
        return _template_response(
            request=request,
            name="settings.html",
            current_user=current_user,
            timezone_value=timezone,
            error=exc.errors()[0].get("msg", "Enter a valid IANA timezone."),
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    update_user_timezone(db=db, user=current_user, timezone=payload.timezone)
    return RedirectResponse(
        url="/settings?updated=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post(
    "/settings/email-reminders",
    response_class=HTMLResponse,
    response_model=None,
    dependencies=[Depends(require_csrf)],
)
def update_email_reminder_setting(
    request: Request,
    email_reminders_enabled: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()

    current_user.email_reminders_enabled = email_reminders_enabled is not None
    if not current_user.email_reminders_enabled:
        disable_email_reminders_for_owner(db=db, owner_id=current_user.id)
    db.commit()
    return RedirectResponse(
        url="/settings?email_reminders_updated=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post(
    "/settings/calendar-feed-token",
    response_class=HTMLResponse,
    response_model=None,
    dependencies=[Depends(require_csrf)],
)
def regenerate_calendar_feed_token_setting(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()
    token = generate_calendar_feed_token(db=db, user=current_user)
    return _template_response(
        request=request,
        name="settings.html",
        current_user=current_user,
        timezone_value=current_user.timezone,
        calendar_feed_configured=True,
        calendar_feed_token=token,
    )


@router.post(
    "/settings/calendar-feed-token/revoke",
    response_class=HTMLResponse,
    response_model=None,
    dependencies=[Depends(require_csrf)],
)
def revoke_calendar_feed_token_setting(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()
    revoke_calendar_feed_token(db=db, user=current_user)
    return RedirectResponse(
        url="/settings?calendar_feed_revoked=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get(
    "/knowledge",
    response_class=HTMLResponse,
    response_model=None,
)
def knowledge_page(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()
    if not settings.knowledge_enabled:
        return _knowledge_disabled_response(
            request=request,
            current_user=current_user,
        )

    return _render_knowledge_page(
        request=request,
        db=db,
        current_user=current_user,
    )


@router.post(
    "/knowledge/conversations",
    response_class=HTMLResponse,
    response_model=None,
    dependencies=[Depends(require_csrf)],
)
def create_knowledge_conversation_submit(
    request: Request,
    title: str = Form(""),
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()
    if not settings.knowledge_enabled:
        return _knowledge_disabled_response(
            request=request,
            current_user=current_user,
        )

    try:
        payload = KnowledgeConversationCreate(title=title)
    except ValidationError as exc:
        return _render_knowledge_page(
            request=request,
            db=db,
            current_user=current_user,
            title_value=title,
            error=exc.errors()[0].get("msg", "Invalid conversation title."),
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    conversation = create_conversation(
        db=db,
        owner_id=current_user.id,
        title=payload.title,
    )
    return RedirectResponse(
        url=f"/knowledge/conversations/{conversation.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post(
    "/knowledge/conversations/{conversation_id}/delete",
    response_class=HTMLResponse,
    response_model=None,
    dependencies=[Depends(require_csrf)],
)
def delete_knowledge_conversation_submit(
    conversation_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()
    if not settings.knowledge_enabled:
        return _knowledge_disabled_response(
            request=request,
            current_user=current_user,
        )

    try:
        delete_conversation(
            db=db,
            owner_id=current_user.id,
            conversation_id=conversation_id,
        )
    except LookupError as exc:
        return _render_knowledge_page(
            request=request,
            db=db,
            current_user=current_user,
            error=str(exc),
            status_code=status.HTTP_404_NOT_FOUND,
        )

    return RedirectResponse(
        url="/knowledge",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get(
    "/knowledge/conversations/{conversation_id}",
    response_class=HTMLResponse,
    response_model=None,
)
def knowledge_conversation_page(
    conversation_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()
    if not settings.knowledge_enabled:
        return _knowledge_disabled_response(
            request=request,
            current_user=current_user,
        )

    return _render_knowledge_conversation(
        request=request,
        db=db,
        current_user=current_user,
        conversation_id=conversation_id,
    )


@router.post(
    "/knowledge/conversations/{conversation_id}/messages",
    response_class=HTMLResponse,
    response_model=None,
    dependencies=[Depends(require_csrf)],
)
def ask_knowledge_question_submit(
    conversation_id: int,
    request: Request,
    question: str = Form(...),
    document_id: str = Form(""),
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()
    if not settings.knowledge_enabled:
        return _knowledge_disabled_response(
            request=request,
            current_user=current_user,
        )

    try:
        payload = KnowledgeQuestionCreate(
            question=question,
            document_id=document_id.strip() or None,
        )
        if payload.document_id is not None:
            selectable_document = db.scalar(
                select(Document.id)
                .join(DocumentIndexJob, DocumentIndexJob.document_id == Document.id)
                .where(
                    Document.id == payload.document_id,
                    Document.owner_id == current_user.id,
                    Document.deleted_at.is_(None),
                    Document.status == DocumentStatus.completed,
                    Document.processing_mode == ProcessingMode.standard,
                    Document.raw_text.is_not(None),
                    DocumentIndexJob.status == DocumentIndexJobStatus.completed,
                )
            )
            if selectable_document is None:
                raise ValueError("Choose one of your indexed documents.")
        enforce_knowledge_question_rate_limit(user_id=current_user.id)
        answer_conversation_question(
            db=db,
            owner_id=current_user.id,
            conversation_id=conversation_id,
            question=payload.question,
            document_id=payload.document_id,
        )
    except (ValidationError, ValueError) as exc:
        error = (
            exc.errors()[0].get("msg", "Invalid question.")
            if isinstance(exc, ValidationError)
            else str(exc)
        )
        return _render_knowledge_conversation(
            request=request,
            db=db,
            current_user=current_user,
            conversation_id=conversation_id,
            question_value=question,
            selected_document_id=(int(document_id) if document_id.isdigit() else None),
            error=error,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    except HTTPException as exc:
        return _render_knowledge_conversation(
            request=request,
            db=db,
            current_user=current_user,
            conversation_id=conversation_id,
            question_value=question,
            error=_exception_message(exc),
            status_code=exc.status_code,
        )
    except LookupError as exc:
        return _render_knowledge_conversation(
            request=request,
            db=db,
            current_user=current_user,
            conversation_id=conversation_id,
            question_value=question,
            error=str(exc),
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except RuntimeError as exc:
        return _render_knowledge_conversation(
            request=request,
            db=db,
            current_user=current_user,
            conversation_id=conversation_id,
            question_value=question,
            error=str(exc),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    return RedirectResponse(
        url=f"/knowledge/conversations/{conversation_id}#conversation-end",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post(
    "/knowledge/documents/{document_id}/reindex",
    response_class=HTMLResponse,
    response_model=None,
    dependencies=[Depends(require_csrf)],
)
def reindex_knowledge_document_submit(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()
    if not settings.knowledge_enabled:
        return _knowledge_disabled_response(
            request=request,
            current_user=current_user,
        )

    document = db.get(Document, document_id)
    if (
        document is None
        or document.owner_id != current_user.id
        or document.deleted_at is not None
    ):
        return _render_knowledge_page(
            request=request,
            db=db,
            current_user=current_user,
            error="Document not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if (
        document.status != DocumentStatus.completed
        or document.processing_mode != ProcessingMode.standard
        or not document.raw_text
    ):
        return _render_knowledge_page(
            request=request,
            db=db,
            current_user=current_user,
            error="Only completed standard documents with extracted text can be indexed.",
            status_code=status.HTTP_409_CONFLICT,
        )

    if document.index_job and document.index_job.status in {
        DocumentIndexJobStatus.pending,
        DocumentIndexJobStatus.running,
    }:
        return _render_knowledge_page(
            request=request,
            db=db,
            current_user=current_user,
            error="Document indexing is already in progress.",
            status_code=status.HTTP_409_CONFLICT,
        )

    enqueue_document_index_job(db=db, document=document)
    return RedirectResponse(
        url="/knowledge?reindexed=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _calendar_month(value: str | None, *, today: date | None = None) -> date:
    if value:
        try:
            parsed = datetime.strptime(value, "%Y-%m").date()
            return parsed.replace(day=1)
        except ValueError:
            pass
    return (today or date.today()).replace(day=1)


def _shift_calendar_month(value: date, months: int) -> date:
    month_number = value.year * 12 + value.month - 1 + months
    return date(month_number // 12, month_number % 12 + 1, 1)


def _calendar_month_bounds(value: date) -> tuple[date, date]:
    next_month = _shift_calendar_month(value, 1)
    return value, next_month - timedelta(days=1)


def _calendar_today(timezone_name: str) -> date:
    return datetime.now(ZoneInfo(validate_iana_timezone(timezone_name))).date()


def _calendar_event_date_range(
    event: CalendarEvent,
    timezone_name: str,
) -> tuple[date, date]:
    if event.all_day:
        assert event.start_date is not None
        return event.start_date, event.end_date or event.start_date
    assert event.start_at is not None
    start_day = _to_user_timezone(event.start_at, timezone_name).date()
    end_day = _to_user_timezone(event.end_at or event.start_at, timezone_name).date()
    return start_day, end_day


def _calendar_event_day(event: CalendarEvent, timezone_name: str) -> date:
    return _calendar_event_date_range(event, timezone_name)[0]


def _calendar_query_url(
    *,
    month: date,
    view: str,
    event_type: CalendarEventType | None,
    status_filter: CalendarEventStatus | None,
    source: CalendarEventSource | None,
    document_id: int | None,
) -> str:
    query = {
        "month": month.strftime("%Y-%m"),
        "view": view,
        "event_type": event_type.value if event_type else None,
        "status": status_filter.value if status_filter else None,
        "source": source.value if source else None,
        "document_id": document_id,
    }
    return "/calendar?" + urlencode(
        {key: value for key, value in query.items() if value is not None}
    )


def _render_calendar_event_detail(
    *,
    request: Request,
    db: Session,
    current_user: User,
    event_id: int,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    try:
        event = calendar_events.get_event(
            db=db,
            owner_id=current_user.id,
            event_id=event_id,
        )
    except calendar_events.CalendarEventNotFoundError:
        return _template_response(
            request=request,
            name="error.html",
            current_user=current_user,
            error="Calendar event not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return _template_response(
        request=request,
        name="calendar_event_detail.html",
        current_user=current_user,
        event=event,
        reminders=list(
            db.scalars(
                select(EventReminder)
                .where(
                    EventReminder.event_id == event.id,
                    EventReminder.status == EventReminderStatus.pending,
                )
                .order_by(EventReminder.offset_minutes, EventReminder.channel)
            ).all()
        ),
        error=error,
        status_code=status_code,
    )


def _calendar_documents(*, db: Session, owner_id: int) -> list[Document]:
    return list(
        db.scalars(
            select(Document)
            .where(
                Document.owner_id == owner_id,
                Document.deleted_at.is_(None),
            )
            .order_by(Document.original_filename.asc(), Document.id.asc())
        ).all()
    )


def _calendar_form_values(
    *,
    event: CalendarEvent | None,
    timezone_name: str,
    document_id: int | None = None,
    start_date: date | None = None,
) -> dict[str, object]:
    if event is None:
        return {
            "title": "",
            "description": "",
            "event_type": CalendarEventType.custom.value,
            "all_day": True,
            "start_date": (start_date or _calendar_today(timezone_name)).isoformat(),
            "end_date": "",
            "start_time": "",
            "end_time": "",
            "timezone": timezone_name,
            "document_id": document_id or "",
            "reminder_settings": [],
            "email_reminders": False,
        }

    event_timezone = event.timezone or timezone_name
    if event.all_day:
        start_date = event.start_date.isoformat() if event.start_date else ""
        end_date = event.end_date.isoformat() if event.end_date else ""
        start_time = ""
        end_time = ""
    else:
        assert event.start_at is not None
        start_at = _to_user_timezone(event.start_at, event_timezone)
        end_at = _to_user_timezone(event.end_at, event_timezone) if event.end_at else None
        start_date = ""
        end_date = ""
        start_time = start_at.strftime("%Y-%m-%dT%H:%M")
        end_time = end_at.strftime("%Y-%m-%dT%H:%M") if end_at else ""

    return {
        "title": event.title,
        "description": event.description or "",
        "event_type": event.event_type.value,
        "all_day": event.all_day,
        "start_date": start_date,
        "end_date": end_date,
        "start_time": start_time,
        "end_time": end_time,
        "timezone": event_timezone,
        "document_id": event.document_id or "",
        "reminder_settings": [],
        "email_reminders": False,
    }


def _render_calendar_event_form(
    *,
    request: Request,
    db: Session,
    current_user: User,
    event: CalendarEvent | None = None,
    document_id: int | None = None,
    start_date: date | None = None,
    values: dict[str, object] | None = None,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    timezone_name = _get_user_timezone_name(current_user)
    form_values = values or _calendar_form_values(
        event=event,
        timezone_name=timezone_name,
        document_id=document_id,
        start_date=start_date,
    )
    if event is not None and values is None:
        form_values["reminder_settings"] = [str(item.offset_minutes) for item in db.scalars(
            select(EventReminder)
            .where(
                EventReminder.event_id == event.id,
                EventReminder.channel == EventReminderChannel.in_app,
                EventReminder.status == EventReminderStatus.pending,
            )
            .order_by(EventReminder.offset_minutes)
        ).all()]
        form_values["email_reminders"] = db.scalar(
            select(EventReminder.id).where(
                EventReminder.event_id == event.id,
                EventReminder.channel == EventReminderChannel.email,
                EventReminder.status == EventReminderStatus.pending,
            )
        ) is not None
    today = _calendar_today(timezone_name)
    start_value = str(form_values["start_date"] or form_values["start_time"] or "")
    start_day = date.fromisoformat(start_value[:10]) if start_value else None
    return _template_response(
        request=request,
        name="calendar_event_form.html",
        current_user=current_user,
        event=event,
        form_values=form_values,
        documents=_calendar_documents(db=db, owner_id=current_user.id),
        calendar_event_types=CalendarEventType,
        calendar_today=today.isoformat(),
        error=error,
        is_past=bool(start_day and start_day < today),
        email_reminders_available=(
            settings.email_reminder_delivery_available
            and current_user.email_reminders_enabled
        ),
        status_code=status_code,
    )


def _parse_calendar_event_form(
    *,
    title: str,
    description: str,
    event_type: CalendarEventType,
    all_day: bool,
    start_date: str,
    end_date: str,
    start_time: str,
    end_time: str,
    timezone_name: str,
    document_id: str,
    expected_sequence: int | None = None,
) -> CalendarEventCreate | CalendarEventUpdate:
    parsed_document_id = int(document_id) if document_id.strip() else None
    if all_day:
        values: dict[str, object] = {
            "title": title,
            "description": _blank_to_none(description),
            "event_type": event_type,
            "all_day": True,
            "start_date": date.fromisoformat(start_date),
            "end_date": date.fromisoformat(end_date) if end_date else None,
            "start_at": None,
            "end_at": None,
            "timezone": None,
            "document_id": parsed_document_id,
        }
    else:
        start_at = _parse_local_calendar_datetime(
            start_time,
            timezone_name=timezone_name,
            field_label="Start time",
        )
        end_at = (
            _parse_local_calendar_datetime(
                end_time,
                timezone_name=timezone_name,
                field_label="End time",
            )
            if end_time
            else None
        )
        values = {
            "title": title,
            "description": _blank_to_none(description),
            "event_type": event_type,
            "all_day": False,
            "start_date": None,
            "end_date": None,
            "start_at": start_at,
            "end_at": end_at,
            "timezone": timezone_name,
            "document_id": parsed_document_id,
        }
    if expected_sequence is None:
        return CalendarEventCreate.model_validate(values)
    return CalendarEventUpdate.model_validate(
        {**values, "expected_sequence": expected_sequence}
    )


def _parse_local_calendar_datetime(
    value: str,
    *,
    timezone_name: str,
    field_label: str,
) -> datetime:
    local_value = datetime.fromisoformat(value)
    if local_value.tzinfo is not None:
        raise ValueError(f"{field_label} must be a local date and time.")

    zone = ZoneInfo(validate_iana_timezone(timezone_name))
    valid_candidates: dict[datetime, datetime] = {}
    for fold in (0, 1):
        candidate = local_value.replace(tzinfo=zone, fold=fold)
        utc_candidate = candidate.astimezone(timezone.utc)
        round_trip = utc_candidate.astimezone(zone).replace(tzinfo=None)
        if round_trip == local_value:
            valid_candidates[utc_candidate] = candidate

    if not valid_candidates:
        message = (
            f"{field_label} does not exist in {timezone_name} because of a "
            "daylight-saving transition."
        )
        raise ValueError(message)
    if len(valid_candidates) > 1:
        message = (
            f"{field_label} is ambiguous in {timezone_name} because of a "
            "daylight-saving transition."
        )
        raise ValueError(message)
    return next(iter(valid_candidates.values()))


def _calendar_form_error(exc: Exception) -> str:
    if isinstance(exc, calendar_events.CalendarEventValidationError):
        return str(exc.errors[0].get("msg", "Invalid event."))
    if isinstance(exc, ValidationError):
        return str(exc.errors(include_url=False)[0].get("msg", "Invalid event."))
    if isinstance(exc, ValueError):
        return str(exc) or "Invalid event."
    return "Invalid event."


@router.get(
    "/calendar",
    response_class=HTMLResponse,
    response_model=None,
)
def calendar_page(
    request: Request,
    db: Session = Depends(get_db),
    month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    view: str = Query(default="month", pattern=r"^(month|agenda)$"),
    event_type: CalendarEventType | None = None,
    status_filter: CalendarEventStatus | None = Query(default=None, alias="status"),
    source: CalendarEventSource | None = None,
    document_id: int | None = Query(default=None, gt=0),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()

    timezone_name = _get_user_timezone_name(current_user)
    today = _calendar_today(timezone_name)
    active_month = _calendar_month(month, today=today)
    start, end = _calendar_month_bounds(active_month)
    events = calendar_events.list_events(
        db=db,
        owner_id=current_user.id,
        user_timezone=timezone_name,
        query=CalendarRangeQuery(
            start=start,
            end=end,
            event_type=event_type,
            status=status_filter,
            source=source,
            document_id=document_id,
        ),
    )
    events.sort(key=lambda event: (_calendar_event_day(event, timezone_name), event.id))
    events_by_day: dict[date, list[CalendarEvent]] = {}
    for event in events:
        event_start, event_end = _calendar_event_date_range(event, timezone_name)
        current_day = max(event_start, start)
        last_day = min(event_end, end)
        while current_day <= last_day:
            events_by_day.setdefault(current_day, []).append(event)
            current_day += timedelta(days=1)

    calendar_weeks = [
        [
            date(active_month.year, active_month.month, day_number)
            if day_number
            else None
            for day_number in week
        ]
        for week in monthcalendar(active_month.year, active_month.month)
    ]
    documents = _calendar_documents(db=db, owner_id=current_user.id)
    return _template_response(
        request=request,
        name="calendar.html",
        current_user=current_user,
        active_month=active_month,
        today=today,
        view=view,
        events=events,
        events_by_day=events_by_day,
        calendar_weeks=calendar_weeks,
        calendar_weekdays=("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
        calendar_event_types=CalendarEventType,
        calendar_statuses=CalendarEventStatus,
        calendar_sources=CalendarEventSource,
        documents=documents,
        filters={
            "event_type": event_type.value if event_type else "",
            "status": status_filter.value if status_filter else "",
            "source": source.value if source else "",
            "document_id": document_id,
        },
        previous_month_url=_calendar_query_url(
            month=_shift_calendar_month(active_month, -1),
            view=view,
            event_type=event_type,
            status_filter=status_filter,
            source=source,
            document_id=document_id,
        ),
        next_month_url=_calendar_query_url(
            month=_shift_calendar_month(active_month, 1),
            view=view,
            event_type=event_type,
            status_filter=status_filter,
            source=source,
            document_id=document_id,
        ),
        today_url=_calendar_query_url(
            month=today.replace(day=1),
            view=view,
            event_type=event_type,
            status_filter=status_filter,
            source=source,
            document_id=document_id,
        ),
    )


@router.get(
    "/calendar/new",
    response_class=HTMLResponse,
    response_model=None,
)
def new_calendar_event_page(
    request: Request,
    document_id: int | None = Query(default=None, gt=0),
    start_date: date | None = Query(default=None),
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()
    return _render_calendar_event_form(
        request=request,
        db=db,
        current_user=current_user,
        document_id=document_id,
        start_date=start_date,
    )


@router.post(
    "/calendar/events",
    response_class=HTMLResponse,
    response_model=None,
    dependencies=[Depends(require_csrf)],
)
def create_calendar_event_submit(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    event_type: CalendarEventType = Form(...),
    all_day: str | None = Form(default=None),
    start_date: str = Form(""),
    end_date: str = Form(""),
    start_time: str = Form(""),
    end_time: str = Form(""),
    timezone_name: str = Form(""),
    document_id: str = Form(""),
    reminder_settings: list[str] = Form(default=[]),
    email_reminders: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()
    is_all_day = all_day is not None
    values = {
        "title": title,
        "description": description,
        "event_type": event_type.value,
        "all_day": is_all_day,
        "start_date": start_date,
        "end_date": end_date,
        "start_time": start_time,
        "end_time": end_time,
        "timezone": timezone_name,
        "document_id": document_id,
        "reminder_settings": reminder_settings,
        "email_reminders": email_reminders is not None,
    }
    try:
        payload = _parse_calendar_event_form(
            title=title,
            description=description,
            event_type=event_type,
            all_day=is_all_day,
            start_date=start_date,
            end_date=end_date,
            start_time=start_time,
            end_time=end_time,
            timezone_name=timezone_name,
            document_id=document_id,
        )
        assert isinstance(payload, CalendarEventCreate)
        event = calendar_events.create_user_event(
            db=db,
            owner_id=current_user.id,
            payload=payload,
        )
        configure_in_app_reminders(
            db=db,
            event=event,
            offset_minutes=_parse_reminder_settings(reminder_settings),
        )
        configure_email_reminders(
            db=db,
            event=event,
            offset_minutes=(
                _parse_reminder_settings(reminder_settings)
                if email_reminders is not None
                else set()
            ),
            recipient_email=current_user.email,
        )
    except (ValidationError, ValueError, calendar_events.CalendarDocumentNotFoundError) as exc:
        return _render_calendar_event_form(
            request=request,
            db=db,
            current_user=current_user,
            values=values,
            error=_calendar_form_error(exc),
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    return RedirectResponse(
        url=f"/calendar/events/{event.id}?created=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get(
    "/calendar/events/{event_id}",
    response_class=HTMLResponse,
    response_model=None,
)
def calendar_event_detail_page(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()
    return _render_calendar_event_detail(
        request=request,
        db=db,
        current_user=current_user,
        event_id=event_id,
    )


@router.get(
    "/calendar/events/{event_id}/download.ics",
    response_model=None,
)
def download_calendar_event_web(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    """Cookie-authenticated browser download for provider-neutral calendar import."""
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()
    try:
        event = calendar_events.get_event(
            db=db,
            owner_id=current_user.id,
            event_id=event_id,
        )
    except calendar_events.CalendarEventNotFoundError:
        return _template_response(
            request=request,
            name="error.html",
            current_user=current_user,
            error="Calendar event not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return Response(
        content=serialize_event_calendar(event),
        media_type=ICAL_CONTENT_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="docsflow-event-{event.id}.ics"',
            "Cache-Control": "private, no-store",
        },
    )


@router.get(
    "/calendar/events/{event_id}/edit",
    response_class=HTMLResponse,
    response_model=None,
)
def edit_calendar_event_page(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()
    try:
        event = calendar_events.get_event(
            db=db,
            owner_id=current_user.id,
            event_id=event_id,
        )
    except calendar_events.CalendarEventNotFoundError:
        return _render_calendar_event_detail(
            request=request,
            db=db,
            current_user=current_user,
            event_id=event_id,
            error="Calendar event not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return _render_calendar_event_form(
        request=request,
        db=db,
        current_user=current_user,
        event=event,
    )


@router.post(
    "/calendar/events/{event_id}/edit",
    response_class=HTMLResponse,
    response_model=None,
    dependencies=[Depends(require_csrf)],
)
def edit_calendar_event_submit(
    event_id: int,
    request: Request,
    sequence: int = Form(..., ge=0),
    title: str = Form(...),
    description: str = Form(""),
    event_type: CalendarEventType = Form(...),
    all_day: str | None = Form(default=None),
    start_date: str = Form(""),
    end_date: str = Form(""),
    start_time: str = Form(""),
    end_time: str = Form(""),
    timezone_name: str = Form(""),
    document_id: str = Form(""),
    reminder_settings: list[str] = Form(default=[]),
    email_reminders: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()
    try:
        event = calendar_events.get_event(
            db=db,
            owner_id=current_user.id,
            event_id=event_id,
        )
    except calendar_events.CalendarEventNotFoundError:
        return _render_calendar_event_detail(
            request=request,
            db=db,
            current_user=current_user,
            event_id=event_id,
            error="Calendar event not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    is_all_day = all_day is not None
    values = {
        "title": title,
        "description": description,
        "event_type": event_type.value,
        "all_day": is_all_day,
        "start_date": start_date,
        "end_date": end_date,
        "start_time": start_time,
        "end_time": end_time,
        "timezone": timezone_name,
        "document_id": document_id,
        "reminder_settings": reminder_settings,
        "email_reminders": email_reminders is not None,
    }
    try:
        payload = _parse_calendar_event_form(
            title=title,
            description=description,
            event_type=event_type,
            all_day=is_all_day,
            start_date=start_date,
            end_date=end_date,
            start_time=start_time,
            end_time=end_time,
            timezone_name=timezone_name,
            document_id=document_id,
            expected_sequence=sequence,
        )
        assert isinstance(payload, CalendarEventUpdate)
        calendar_events.update_event(
            db=db,
            owner_id=current_user.id,
            event_id=event_id,
            payload=payload,
        )
        configure_in_app_reminders(
            db=db,
            event=event,
            offset_minutes=_parse_reminder_settings(reminder_settings),
        )
        configure_email_reminders(
            db=db,
            event=event,
            offset_minutes=(
                _parse_reminder_settings(reminder_settings)
                if email_reminders is not None
                else set()
            ),
            recipient_email=current_user.email,
        )
    except (
        ValidationError,
        ValueError,
        calendar_events.CalendarDocumentNotFoundError,
        calendar_events.CalendarEventConflictError,
        calendar_events.CalendarEventValidationError,
    ) as exc:
        return _render_calendar_event_form(
            request=request,
            db=db,
            current_user=current_user,
            event=event,
            values=values,
            error=_calendar_form_error(exc),
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    return RedirectResponse(
        url=f"/calendar/events/{event_id}?saved=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post(
    "/calendar/events/{event_id}/confirm",
    response_class=HTMLResponse,
    response_model=None,
    dependencies=[Depends(require_csrf)],
)
def confirm_calendar_event_submit(
    event_id: int,
    request: Request,
    sequence: int = Form(..., ge=0),
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()
    try:
        calendar_events.confirm_event(
            db=db,
            owner_id=current_user.id,
            event_id=event_id,
            expected_sequence=sequence,
        )
    except (calendar_events.CalendarEventNotFoundError, calendar_events.CalendarEventConflictError) as exc:
        return _render_calendar_event_detail(
            request=request,
            db=db,
            current_user=current_user,
            event_id=event_id,
            error=str(exc),
            status_code=status.HTTP_409_CONFLICT,
        )
    return RedirectResponse(
        url=f"/calendar/events/{event_id}?confirmed=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post(
    "/calendar/events/{event_id}/dismiss",
    response_class=HTMLResponse,
    response_model=None,
    dependencies=[Depends(require_csrf)],
)
def dismiss_calendar_suggestion_submit(
    event_id: int,
    request: Request,
    sequence: int = Form(..., ge=0),
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()
    try:
        calendar_events.cancel_event(
            db=db,
            owner_id=current_user.id,
            event_id=event_id,
            expected_sequence=sequence,
        )
    except (calendar_events.CalendarEventNotFoundError, calendar_events.CalendarEventConflictError) as exc:
        return _render_calendar_event_detail(
            request=request,
            db=db,
            current_user=current_user,
            event_id=event_id,
            error=str(exc),
            status_code=status.HTTP_409_CONFLICT,
        )
    return RedirectResponse(
        url="/calendar?dismissed=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post(
    "/calendar/events/{event_id}/delete",
    response_class=HTMLResponse,
    response_model=None,
    dependencies=[Depends(require_csrf)],
)
def delete_calendar_event_submit(
    event_id: int,
    request: Request,
    sequence: int = Form(..., ge=0),
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()
    try:
        calendar_events.delete_event(
            db=db,
            owner_id=current_user.id,
            event_id=event_id,
            expected_sequence=sequence,
        )
    except (calendar_events.CalendarEventNotFoundError, calendar_events.CalendarEventConflictError) as exc:
        return _render_calendar_event_detail(
            request=request,
            db=db,
            current_user=current_user,
            event_id=event_id,
            error=str(exc),
            status_code=status.HTTP_409_CONFLICT,
        )
    return RedirectResponse(
        url="/calendar?deleted=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post(
    "/documents/{document_id}/calendar/from-deadline",
    response_class=HTMLResponse,
    response_model=None,
    dependencies=[Depends(require_csrf)],
)
def create_calendar_event_from_deadline_submit(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()
    document = db.get(Document, document_id)
    if (
        document is None
        or document.owner_id != current_user.id
        or document.deleted_at is not None
    ):
        return _render_document_detail(
            request=request,
            db=db,
            current_user=current_user,
            document_id=document_id,
            error="Document not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if document.deadline is None:
        return _render_document_detail(
            request=request,
            db=db,
            current_user=current_user,
            document_id=document_id,
            error="This document does not have a deadline to add to the calendar.",
            status_code=status.HTTP_409_CONFLICT,
        )
    event = calendar_events.create_user_event(
        db=db,
        owner_id=current_user.id,
        payload=CalendarEventCreate(
            title=f"Deadline: {document.original_filename}"[:255],
            event_type=CalendarEventType.action_deadline,
            start_date=document.deadline,
            document_id=document.id,
        ),
    )
    return RedirectResponse(
        url=f"/calendar/events/{event.id}?created_from_deadline=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get(
    "/documents",
    response_class=HTMLResponse,
    response_model=None,
)
def documents_page(
    request: Request,
    db: Session = Depends(get_db),
    query: str | None = Query(default=None, max_length=500),
    document_type: str | None = Query(default=None, max_length=50),
    status_filter: DocumentStatus | None = Query(default=None, alias="status"),
    document_date_from: date | None = None,
    document_date_to: date | None = None,
    uploaded_from: date | None = None,
    uploaded_to: date | None = None,
    amount_min: Decimal | None = Query(default=None, ge=0),
    amount_max: Decimal | None = Query(default=None, ge=0),
    deadline_from: date | None = None,
    deadline_to: date | None = None,
    requires_action: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    sort_by: str = Query(default="created_at", max_length=50),
    sort_direction: SortDirection = "desc",
) -> Response:
    current_user = _get_web_current_user(request, db)

    if current_user is None:
        return _redirect_to_login()

    sort_by = normalize_document_sort_field(sort_by)
    result = api_list_my_documents(
        db=db,
        current_user=current_user,
        query=query,
        document_type=document_type,
        status_filter=status_filter,
        document_date_from=document_date_from,
        document_date_to=document_date_to,
        uploaded_from=uploaded_from,
        uploaded_to=uploaded_to,
        amount_min=amount_min,
        amount_max=amount_max,
        deadline_from=deadline_from,
        deadline_to=deadline_to,
        requires_action=requires_action,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_direction=sort_direction,
    )
    return _template_response(
        request=request,
        name="documents.html",
        current_user=current_user,
        documents=result.items,
        total_documents=result.total,
        page=result.page,
        total_pages=result.total_pages,
        filters={
            "query": query or "",
            "document_type": document_type or "",
            "status": status_filter.value if status_filter else "",
            "document_date_from": document_date_from.isoformat() if document_date_from else "",
            "document_date_to": document_date_to.isoformat() if document_date_to else "",
            "uploaded_from": uploaded_from.isoformat() if uploaded_from else "",
            "uploaded_to": uploaded_to.isoformat() if uploaded_to else "",
            "amount_min": amount_min if amount_min is not None else "",
            "amount_max": amount_max if amount_max is not None else "",
            "deadline_from": deadline_from.isoformat() if deadline_from else "",
            "deadline_to": deadline_to.isoformat() if deadline_to else "",
            "requires_action": requires_action,
            "page_size": page_size,
            "sort_by": sort_by,
            "sort_direction": sort_direction,
        },
        document_types=DOCUMENT_TYPES,
        document_statuses=DocumentStatus,
        pagination_query=_pagination_query(request),
        document_sort_query=_document_sort_query(request),
        deleted=request.query_params.get("deleted") == "1",
    )


@router.get(
    "/documents/upload",
    response_class=HTMLResponse,
    response_model=None,
)
def upload_page(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)

    if current_user is None:
        return _redirect_to_login()

    return _template_response(
        request=request,
        name="upload.html",
        current_user=current_user,
    )


@router.post(
    "/documents/upload",
    response_class=HTMLResponse,
    response_model=None,
    dependencies=[Depends(require_csrf)],
)
async def upload_submit(
    request: Request,
    file: UploadFile = File(...),
    confidential: bool = Form(False),
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)

    if current_user is None:
        return _redirect_to_login()

    try:
        enforce_upload_rate_limit(current_user)
        document = await api_upload_document(
            db=db,
            current_user=current_user,
            file=file,
            confidential=confidential,
            _=None,
        )
    except HTTPException as exc:
        return _template_response(
            request=request,
            name="upload.html",
            current_user=current_user,
            error=_exception_message(exc),
            confidential=confidential,
            status_code=exc.status_code,
        )

    return RedirectResponse(
        url=f"/documents/{document.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get(
    "/documents/{document_id}",
    response_class=HTMLResponse,
    response_model=None,
)
def document_detail_page(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)

    if current_user is None:
        return _redirect_to_login()

    return _render_document_detail(
        request=request,
        db=db,
        current_user=current_user,
        document_id=document_id,
    )


@router.post(
    "/documents/{document_id}/correct",
    response_class=HTMLResponse,
    response_model=None,
    dependencies=[Depends(require_csrf)],
)
def correct_document_submit(
    document_id: int,
    request: Request,
    document_type: str = Form(...),
    summary: str = Form(""),
    sender: str = Form(""),
    amount: str = Form(""),
    currency: str = Form(""),
    document_date: str = Form(""),
    deadline: str = Form(""),
    confidence_score: str = Form(""),
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)

    if current_user is None:
        return _redirect_to_login()

    try:
        correction = DocumentCorrection(
            document_type=_blank_to_none(document_type),
            summary=_blank_to_none(summary),
            sender=_blank_to_none(sender),
            amount=_blank_to_none(amount),
            currency=_blank_to_none(currency),
            document_date=_blank_to_none(document_date),
            deadline=_blank_to_none(deadline),
            confidence_score=_blank_to_none(confidence_score),
        )
        api_correct_document_result(
            document_id=document_id,
            correction=correction,
            request=request,
            db=db,
            current_user=current_user,
        )
    except ValidationError as exc:
        error = exc.errors()[0].get(
            "msg",
            "Invalid correction data.",
        )
        return _render_document_detail(
            request=request,
            db=db,
            current_user=current_user,
            document_id=document_id,
            error=error,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    except HTTPException as exc:
        return _render_document_detail(
            request=request,
            db=db,
            current_user=current_user,
            document_id=document_id,
            error=_exception_message(exc),
            status_code=exc.status_code,
        )

    return RedirectResponse(
        url=f"/documents/{document_id}?saved=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post(
    "/documents/{document_id}/note",
    response_class=HTMLResponse,
    response_model=None,
    dependencies=[Depends(require_csrf)],
)
def update_document_note_submit(
    document_id: int,
    request: Request,
    user_note: str = Form(""),
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)

    if current_user is None:
        return _redirect_to_login()

    try:
        api_update_document_note(
            document_id=document_id,
            note=DocumentNoteUpdate(user_note=_blank_to_none(user_note)),
            db=db,
            current_user=current_user,
        )
    except ValidationError as exc:
        error = exc.errors()[0].get("msg", "Invalid note.")
        return _render_document_detail(
            request=request,
            db=db,
            current_user=current_user,
            document_id=document_id,
            error=error,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    except HTTPException as exc:
        return _render_document_detail(
            request=request,
            db=db,
            current_user=current_user,
            document_id=document_id,
            error=_exception_message(exc),
            status_code=exc.status_code,
        )

    return RedirectResponse(
        url=f"/documents/{document_id}?note_saved=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post(
    "/documents/{document_id}/confirm",
    response_class=HTMLResponse,
    response_model=None,
    dependencies=[Depends(require_csrf)],
)
def confirm_document_submit(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)

    if current_user is None:
        return _redirect_to_login()

    try:
        api_confirm_document_extraction(
            document_id=document_id,
            request=request,
            db=db,
            current_user=current_user,
        )
    except HTTPException as exc:
        return _render_document_detail(
            request=request,
            db=db,
            current_user=current_user,
            document_id=document_id,
            error=_exception_message(exc),
            status_code=exc.status_code,
        )

    return RedirectResponse(
        url=f"/documents/{document_id}?confirmed=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post(
    "/documents/{document_id}/reprocess",
    response_class=HTMLResponse,
    response_model=None,
    dependencies=[Depends(require_csrf)],
)
def reprocess_document_submit(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)

    if current_user is None:
        return _redirect_to_login()

    try:
        api_reprocess_document(
            document_id=document_id,
            db=db,
            current_user=current_user,
        )
    except HTTPException as exc:
        return _render_document_detail(
            request=request,
            db=db,
            current_user=current_user,
            document_id=document_id,
            error=_exception_message(exc),
            status_code=exc.status_code,
        )

    return RedirectResponse(
        url=f"/documents/{document_id}?reprocessed=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get(
    "/documents/{document_id}/delete",
    response_class=HTMLResponse,
    response_model=None,
)
def delete_document_confirmation(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)

    if current_user is None:
        return _redirect_to_login()

    try:
        document = api_get_my_document(
            document_id=document_id,
            db=db,
            current_user=current_user,
        )
    except HTTPException as exc:
        return _template_response(
            request=request,
            name="error.html",
            current_user=current_user,
            error=_exception_message(exc),
            status_code=exc.status_code,
        )

    return _template_response(
        request=request,
        name="delete_document.html",
        current_user=current_user,
        document=document,
    )


@router.post(
    "/documents/{document_id}/delete",
    response_class=HTMLResponse,
    response_model=None,
    dependencies=[Depends(require_csrf)],
)
def delete_document_submit(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)

    if current_user is None:
        return _redirect_to_login()

    try:
        api_delete_document(
            document_id=document_id,
            db=db,
            current_user=current_user,
        )
    except HTTPException as exc:
        return _template_response(
            request=request,
            name="error.html",
            current_user=current_user,
            error=_exception_message(exc),
            status_code=exc.status_code,
        )

    return RedirectResponse(
        url="/documents?deleted=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )
