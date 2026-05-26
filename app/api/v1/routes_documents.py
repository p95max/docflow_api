from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.v1.dependencies import CurrentUser, DbSession
from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.processing_job import ProcessingJobStatus
from app.schemas.document import DocumentRead
from app.schemas.processing_job import ProcessingJobRead
from app.services.processing_jobs import (
    create_processing_job,
    enqueue_processing_job,
    list_processing_jobs_for_document,
)
from app.services.storage import (
    build_document_storage_key,
    delete_document_file,
    save_document_file,
)
from app.services.uploads import enforce_upload_rate_limit, read_and_validate_upload_file

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
    file: Annotated[UploadFile, File(description="PDF, JPG or PNG document")],
    confidential: Annotated[
        bool,
        Form(description="Use confidential local-only processing mode"),
    ] = False,
    _: Annotated[None, Depends(check_upload_rate_limit)] = None,
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


@router.get("", response_model=list[DocumentRead])
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


@router.get("/{document_id}", response_model=DocumentRead)
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


@router.get("/{document_id}/jobs", response_model=list[ProcessingJobRead])
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
        for job in list_processing_jobs_for_document(db=db, document_id=document.id)
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

    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    if document.owner_id != current_user.id:
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