from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.v1.dependencies import CurrentUser, DbSession
from app.models.document import Document, DocumentStatus, ProcessingMode
from app.schemas.document import DocumentRead
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
        db.commit()
        db.refresh(document)

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