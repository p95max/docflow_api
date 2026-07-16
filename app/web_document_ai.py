from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.api.v1.routes_document_ai_analysis import analyze_document_with_ai
from app.db.session import get_db
from app.web import (
    _exception_message,
    _get_web_current_user,
    _redirect_to_login,
    _render_document_detail,
    require_csrf,
)

router = APIRouter(include_in_schema=False)


@router.post(
    "/documents/{document_id}/analyze-with-ai",
    response_class=HTMLResponse,
    response_model=None,
    dependencies=[Depends(require_csrf)],
)
def analyze_document_with_ai_submit(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()

    try:
        analyze_document_with_ai(
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
        url=f"/documents/{document_id}?ai_analysis_started=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )
