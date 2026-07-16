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
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

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
from app.db.session import get_db
from app.models.user import User
from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.document_index_job import DocumentIndexJobStatus
from app.models.knowledge_message import KnowledgeMessageRole
from app.schemas.document import DocumentCorrection, DocumentNoteUpdate
from app.schemas.knowledge import KnowledgeConversationCreate, KnowledgeQuestionCreate
from app.services.document_search import DocumentSortField, SortDirection
from app.services.document_index_jobs import enqueue_document_index_job
from app.services.knowledge_conversations import (
    answer_conversation_question,
    create_conversation,
    delete_conversation,
    get_owned_conversation,
    list_conversations,
)
from app.schemas.user import UserCreate
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
BERLIN_TIMEZONE = ZoneInfo("Europe/Berlin")

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
    }.get(status_value, "text-bg-secondary")


def _to_berlin_timezone(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BERLIN_TIMEZONE)


def _format_berlin_datetime(value: datetime) -> str:
    return _to_berlin_timezone(value).strftime("%H:%M %d-%m-%Y")


def _format_berlin_date(value: datetime) -> str:
    return _to_berlin_timezone(value).strftime("%d-%m-%Y")


def _format_berlin_time(value: datetime) -> str:
    return _to_berlin_timezone(value).strftime("%H:%M")


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
    now_berlin = datetime.now(BERLIN_TIMEZONE)
    month_start = now_berlin.replace(
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
            "label": now_berlin.strftime("%B %Y"),
        },
        "configured_models": (
            ("Document extraction", settings.openai_model),
            ("Ask Documents", settings.openai_rag_model),
            ("Embeddings", settings.openai_embedding_model),
        ),
    }


templates.env.filters["file_size"] = _format_file_size
templates.env.filters["status_badge_class"] = _status_badge_class
templates.env.filters["berlin_datetime"] = _format_berlin_datetime
templates.env.filters["berlin_date"] = _format_berlin_date
templates.env.filters["berlin_time"] = _format_berlin_time
templates.env.filters["token_count"] = _format_token_count


def _pagination_query(request: Request) -> str:
    params = [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key != "page"
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
    response = templates.TemplateResponse(
        request=request,
        name=name,
        context={
            "request": request,
            "current_user": current_user,
            "document_types": DOCUMENT_TYPES,
            "knowledge_enabled": settings.knowledge_enabled,
            "csrf_token": csrf_token,
            "ai_usage": _get_ai_usage_summary(current_user),
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
    submitted_token = request.headers.get("X-CSRF-Token")
    if not submitted_token:
        form = await request.form()
        submitted_value = form.get("csrf_token")
        submitted_token = submitted_value if isinstance(submitted_value, str) else None

    if not (
        cookie_token
        and submitted_token
        and secrets.compare_digest(cookie_token, submitted_token)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid CSRF token.",
        )


def _exception_message(exc: HTTPException) -> str:
    if isinstance(exc.detail, dict):
        return str(exc.detail.get("message") or exc.detail)
    return str(exc.detail)


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
        payload = KnowledgeQuestionCreate(question=question)
        enforce_knowledge_question_rate_limit(user_id=current_user.id)
        answer_conversation_question(
            db=db,
            owner_id=current_user.id,
            conversation_id=conversation_id,
            question=payload.question,
        )
    except ValidationError as exc:
        return _render_knowledge_conversation(
            request=request,
            db=db,
            current_user=current_user,
            conversation_id=conversation_id,
            question_value=question,
            error=exc.errors()[0].get("msg", "Invalid question."),
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
    sort_by: DocumentSortField = "created_at",
    sort_direction: SortDirection = "desc",
) -> Response:
    current_user = _get_web_current_user(request, db)

    if current_user is None:
        return _redirect_to_login()

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
