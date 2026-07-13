from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.backup_jobs import (
    create_backup_job,
    enqueue_backup_job,
    list_backup_jobs,
)
from app.web import _get_web_current_user, _redirect_to_login, _template_response

router = APIRouter(include_in_schema=False)


@router.get(
    "/backups",
    response_class=HTMLResponse,
    response_model=None,
)
def backups_page(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()

    jobs = list_backup_jobs(db=db, owner_id=current_user.id)
    return _template_response(
        request=request,
        name="backups.html",
        current_user=current_user,
        backup_jobs=jobs,
        created=request.query_params.get("created") == "1",
    )


@router.post(
    "/backups/run",
    response_class=HTMLResponse,
    response_model=None,
)
def run_backup_submit(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()

    job = create_backup_job(db=db, owner_id=current_user.id)
    db.commit()
    db.refresh(job)

    try:
        enqueue_backup_job(db=db, job=job)
    except Exception as exc:
        jobs = list_backup_jobs(db=db, owner_id=current_user.id)
        return _template_response(
            request=request,
            name="backups.html",
            current_user=current_user,
            backup_jobs=jobs,
            error=f"Backup could not be queued: {exc}",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    return RedirectResponse(
        url="/backups?created=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )
