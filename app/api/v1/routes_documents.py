from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote

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
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.v1.dependencies import CurrentUser, DbSession
from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.processing_job import (
    ProcessingJob,
    ProcessingJobStatus,
)
from app.schemas.document import (
    DocumentCorrection,
    DocumentRead,
    DocumentResultRead,
)
from app.schemas.processing_job import ProcessingJobRead
from app.services.processing_jobs import (
    create_processing_job,
    enqueue_processing_job,
    list_processing_jobs_for_document,
)
from app.services.security import (
    create_document_preview_token,
    decode_document_preview_token,
)
from app.services.storage import (
    build_document_storage_key,
    delete_document_file,
    get_document_file_path,
    save_document_file,
)
from app.services.uploads import (
    enforce_upload_rate_limit,
    read_and_validate_upload_file,
)


router = APIRouter()


def check_upload_rate_limit(current_user: CurrentUser) -> None:
    enforce_upload_rate_limit(current_user)


@router.post(
    "/upload",
    response_model=DocumentRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    db: DbSession,
    current_user: CurrentUser,
    file: Annotated[
        UploadFile,
        File(description="PDF, JPG or PNG document"),
    ],
    confidential: Annotated[
        bool,
        Form(description="Use confidential local-only processing mode"),
    ] = False,
    _: Annotated[
        None,
        Depends(check_upload_rate_limit),
    ] = None,
) -> DocumentRead:
    upload = await read_and_validate_upload_file(file)

    duplicate_document = _get_duplicate_document(
        db=db,
        owner_id=current_user.id,
        checksum_sha256=upload.checksum_sha256,
    )

    if duplicate_document is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Duplicate document upload detected.",
                "document_id": duplicate_document.id,
            },
        )

    document = Document(
        owner_id=current_user.id,
        original_filename=upload.filename,
        status=DocumentStatus.uploaded,
        processing_mode=(
            ProcessingMode.confidential
            if confidential
            else ProcessingMode.standard
        ),
        content_type=upload.content_type,
        file_size_bytes=upload.size_bytes,
        checksum_sha256=upload.checksum_sha256,
    )

    storage_key: str | None = None

    try:
        db.add(document)
        db.flush()

        storage_key = build_document_storage_key(
            document=document,
            extension=upload.extension,
        )

        save_document_file(
            content=upload.content,
            storage_key=storage_key,
        )

        document.storage_key = storage_key

        processing_job = create_processing_job(
            db=db,
            document=document,
        )

        db.commit()
        db.refresh(document)
        db.refresh(processing_job)

    except IntegrityError:
        db.rollback()
        delete_document_file(storage_key)

        duplicate_document = _get_duplicate_document(
            db=db,
            owner_id=current_user.id,
            checksum_sha256=upload.checksum_sha256,
        )

        if duplicate_document is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "Duplicate document upload detected.",
                    "document_id": duplicate_document.id,
                },
            ) from None

        raise

    except Exception:
        db.rollback()
        delete_document_file(storage_key)
        raise

    enqueue_processing_job(
        db=db,
        job=processing_job,
    )

    db.refresh(document)

    return document


@router.get(
    "",
    response_model=list[DocumentRead],
)
def list_my_documents(
    db: DbSession,
    current_user: CurrentUser,
) -> list[DocumentRead]:
    stmt = (
        select(Document)
        .where(Document.owner_id == current_user.id)
        .order_by(Document.created_at.desc())
    )

    return list(db.scalars(stmt).all())


@router.get(
    "/{document_id}/result",
    response_model=DocumentResultRead,
)
def get_document_result(
    document_id: int,
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
) -> DocumentResultRead:
    document = _get_owned_document(
        db=db,
        document_id=document_id,
        current_user=current_user,
    )

    latest_job = _get_latest_processing_job(
        db=db,
        document_id=document.id,
    )

    return _build_document_result(
        document=document,
        latest_job=latest_job,
        request=request,
    )


@router.patch(
    "/{document_id}/result",
    response_model=DocumentResultRead,
)
def correct_document_result(
    document_id: int,
    correction: DocumentCorrection,
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
) -> DocumentResultRead:
    document = _get_owned_document(
        db=db,
        document_id=document_id,
        current_user=current_user,
    )

    if document.status != DocumentStatus.completed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only completed documents can be corrected.",
        )

    if document.processing_mode != ProcessingMode.standard:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Confidential documents do not have "
                "AI extraction results."
            ),
        )

    changes = correction.model_dump(exclude_unset=True)

    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one correction field is required.",
        )

    if changes.get("currency") is not None:
        changes["currency"] = changes["currency"].upper()

    for field_name, value in changes.items():
        setattr(document, field_name, value)

    json_changes = correction.model_dump(
        exclude_unset=True,
        mode="json",
    )

    if json_changes.get("currency") is not None:
        json_changes["currency"] = json_changes["currency"].upper()

    document.manual_corrections = {
        **(document.manual_corrections or {}),
        **json_changes,
    }
    document.manually_corrected_at = datetime.now(UTC)

    db.commit()
    db.refresh(document)

    latest_job = _get_latest_processing_job(
        db=db,
        document_id=document.id,
    )

    return _build_document_result(
        document=document,
        latest_job=latest_job,
        request=request,
    )


@router.get(
    "/{document_id}/preview",
    name="preview_document_file",
    response_class=FileResponse,
)
def preview_document_file(
    document_id: int,
    db: DbSession,
    token: Annotated[str, Query(min_length=1)],
) -> FileResponse:
    try:
        payload = decode_document_preview_token(token)

        token_document_id = int(payload["document_id"])
        token_owner_id = int(payload["sub"])

    except (
        jwt.InvalidTokenError,
        KeyError,
        TypeError,
        ValueError,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired document preview token.",
        ) from None

    if token_document_id != document_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    document = db.get(Document, document_id)

    if document is None or document.owner_id != token_owner_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    if not document.storage_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document file not found",
        )

    file_path = get_document_file_path(document.storage_key)

    if not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document file not found",
        )

    encoded_filename = quote(
        document.original_filename,
        safe="",
    )

    return FileResponse(
        path=file_path,
        media_type=(
            document.content_type
            or "application/octet-stream"
        ),
        headers={
            "Content-Disposition": (
                f"inline; filename*=UTF-8''{encoded_filename}"
            ),
            "Cache-Control": "private, no-store",
        },
    )


@router.get(
    "/{document_id}",
    response_model=DocumentRead,
)
def get_my_document(
    document_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> DocumentRead:
    return _get_owned_document(
        db=db,
        document_id=document_id,
        current_user=current_user,
    )


@router.get(
    "/{document_id}/jobs",
    response_model=list[ProcessingJobRead],
)
def list_document_jobs(
    document_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> list[ProcessingJobRead]:
    document = _get_owned_document(
        db=db,
        document_id=document_id,
        current_user=current_user,
    )

    return list_processing_jobs_for_document(
        db=db,
        document_id=document.id,
    )


@router.post(
    "/{document_id}/reprocess",
    response_model=ProcessingJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def reprocess_document(
    document_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> ProcessingJobRead:
    document = _get_owned_document(
        db=db,
        document_id=document_id,
        current_user=current_user,
    )

    if document.status != DocumentStatus.failed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only failed documents can be reprocessed.",
        )

    failed_jobs = [
        job
        for job in list_processing_jobs_for_document(
            db=db,
            document_id=document.id,
        )
        if job.status == ProcessingJobStatus.failed
    ]

    if not failed_jobs:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document has no failed processing jobs.",
        )

    document.status = DocumentStatus.uploaded

    processing_job = create_processing_job(
        db=db,
        document=document,
    )

    db.commit()
    db.refresh(processing_job)

    return enqueue_processing_job(
        db=db,
        job=processing_job,
    )


def _get_owned_document(
    db: DbSession,
    document_id: int,
    current_user: CurrentUser,
) -> Document:
    document = db.get(Document, document_id)

    if document is None or document.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    return document


def _get_duplicate_document(
    db: DbSession,
    owner_id: int,
    checksum_sha256: str,
) -> Document | None:
    stmt = select(Document).where(
        Document.owner_id == owner_id,
        Document.checksum_sha256 == checksum_sha256,
    )

    return db.scalar(stmt)


def _get_latest_processing_job(
    *,
    db: DbSession,
    document_id: int,
) -> ProcessingJob | None:
    stmt = (
        select(ProcessingJob)
        .where(
            ProcessingJob.document_id == document_id,
        )
        .order_by(
            ProcessingJob.created_at.desc(),
            ProcessingJob.id.desc(),
        )
        .limit(1)
    )

    return db.scalar(stmt)


def _build_document_result(
    *,
    document: Document,
    latest_job: ProcessingJob | None,
    request: Request,
) -> DocumentResultRead:
    file_preview_url: str | None = None
    file_preview_expires_at: datetime | None = None

    if document.storage_key:
        file_path = get_document_file_path(
            document.storage_key,
        )

        if file_path.is_file():
            preview_token, expires_at = (
                create_document_preview_token(
                    document_id=document.id,
                    owner_id=document.owner_id,
                )
            )

            preview_url = request.url_for(
                "preview_document_file",
                document_id=document.id,
            ).include_query_params(
                token=preview_token,
            )

            file_preview_url = str(preview_url)
            file_preview_expires_at = expires_at

    processing_error: str | None = None

    if (
        latest_job is not None
        and latest_job.status == ProcessingJobStatus.failed
    ):
        processing_error = latest_job.error_message

    document_data = DocumentRead.model_validate(
        document,
    ).model_dump()

    return DocumentResultRead(
        **document_data,
        raw_text=document.raw_text,
        file_preview_url=file_preview_url,
        file_preview_expires_at=file_preview_expires_at,
        manual_corrections=document.manual_corrections,
        manually_corrected_at=document.manually_corrected_at,
        latest_job=latest_job,
        processing_error=processing_error,
        can_correct=(
            document.status == DocumentStatus.completed
            and document.processing_mode == ProcessingMode.standard
        ),
        can_reprocess=(
            document.status == DocumentStatus.failed
            and latest_job is not None
            and latest_job.status == ProcessingJobStatus.failed
        ),
    )