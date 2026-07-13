from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import jwt
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.orm import Session

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
    upload_document as api_upload_document,
)
from app.core.config import settings
from app.db.session import get_db
from app.models.user import User
from app.schemas.document import DocumentCorrection
from app.schemas.user import UserCreate
from app.services.security import create_access_token, decode_access_token
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
        "draft": "text-bg-secondary",
        "corrected": "text-bg-warning",
        "confirmed": "text-bg-success",
    }.get(status_value, "text-bg-secondary")


templates.env.filters["file_size"] = _format_file_size
templates.env.filters["status_badge_class"] = _status_badge_class


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
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)


def _is_local_development_host(request: Request) -> bool:
    hostname = request.url.hostname or ""
    return hostname in {"localhost", "127.0.0.1", "0.0.0.0"} or hostname.endswith(
        ".app.github.dev"
    )


def _template_response(
    *,
    request: Request,
    name: str,
    current_user: User | None = None,
    status_code: int = status.HTTP_200_OK,
    **context: object,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name=name,
        context={
            "request": request,
            "current_user": current_user,
            "document_types": DOCUMENT_TYPES,
            **context,
        },
        status_code=status_code,
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
    return RedirectResponse(url=destination, status_code=status.HTTP_302_FOUND)


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    if _get_web_current_user(request, db) is not None:
        return RedirectResponse(
            url="/documents",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    use_test_credentials = _is_local_development_host(request)

    return _template_response(
        request=request,
        name="login.html",
        email_value="m@m.com" if use_test_credentials else "",
        password_value="12345678" if use_test_credentials else "",
        registered=request.query_params.get("registered") == "1",
    )


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
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
            error="Incorrect email or password.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    token = create_access_token(subject=str(user.id))
    response = RedirectResponse(
        url="/documents",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path="/",
    )
    return response


@router.post("/logout")
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


@router.get("/register", response_class=HTMLResponse)
def register_page(
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
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


@router.post("/register", response_class=HTMLResponse)
def register_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    try:
        payload = UserCreate(email=email, password=password)
    except ValidationError as exc:
        error = exc.errors()[0].get("msg", "Invalid registration data.")
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

    create_user(
        db=db,
        email=str(payload.email),
        password=payload.password,
    )

    return RedirectResponse(
        url="/login?registered=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/documents", response_class=HTMLResponse)
def documents_page(
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    current_user = _get_web_current_user(request, db)

    if current_user is None:
        return _redirect_to_login()

    documents = api_list_my_documents(
        db=db,
        current_user=current_user,
    )

    return _template_response(
        request=request,
        name="documents.html",
        current_user=current_user,
        documents=documents,
        deleted=request.query_params.get("deleted") == "1",
    )


@router.get("/documents/upload", response_class=HTMLResponse)
def upload_page(
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    current_user = _get_web_current_user(request, db)

    if current_user is None:
        return _redirect_to_login()

    return _template_response(
        request=request,
        name="upload.html",
        current_user=current_user,
    )


@router.post("/documents/upload", response_class=HTMLResponse)
async def upload_submit(
    request: Request,
    file: UploadFile = File(...),
    confidential: bool = Form(False),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
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


@router.get("/documents/{document_id}", response_class=HTMLResponse)
def document_detail_page(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    current_user = _get_web_current_user(request, db)

    if current_user is None:
        return _redirect_to_login()

    return _render_document_detail(
        request=request,
        db=db,
        current_user=current_user,
        document_id=document_id,
    )


@router.post("/documents/{document_id}/correct", response_class=HTMLResponse)
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
) -> HTMLResponse | RedirectResponse:
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
        error = exc.errors()[0].get("msg", "Invalid correction data.")
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


@router.post("/documents/{document_id}/confirm", response_class=HTMLResponse)
def confirm_document_submit(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
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


@router.post("/documents/{document_id}/reprocess", response_class=HTMLResponse)
def reprocess_document_submit(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
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
)
def delete_document_confirmation(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
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


@router.post("/documents/{document_id}/delete", response_class=HTMLResponse)
def delete_document_submit(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
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
