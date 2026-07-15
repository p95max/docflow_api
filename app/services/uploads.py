import hashlib
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from app.core.config import settings
from app.models.user import User
from app.services.rate_limits import enforce_upload_rate_limit as enforce_shared_upload_rate_limit

ALLOWED_UPLOAD_MIME_TYPES = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/png": "png",
}

UPLOAD_CHUNK_SIZE = 1024 * 1024

@dataclass(frozen=True)
class ValidatedUpload:
    filename: str
    content_type: str
    extension: str
    size_bytes: int
    checksum_sha256: str
    content: bytes


def enforce_upload_rate_limit(current_user: User) -> None:
    """Apply the shared Redis-backed per-user upload limit."""
    enforce_shared_upload_rate_limit(user_id=current_user.id)


async def read_and_validate_upload_file(upload_file: UploadFile) -> ValidatedUpload:
    """Read an uploaded file safely and validate size, MIME type and file signature."""
    filename = Path(upload_file.filename or "").name

    if not filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename is required.",
        )

    content_type = upload_file.content_type

    if content_type not in ALLOWED_UPLOAD_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported file type. Only PDF, JPG and PNG files are allowed.",
        )

    checksum = hashlib.sha256()
    data = bytearray()

    while chunk := await upload_file.read(UPLOAD_CHUNK_SIZE):
        data.extend(chunk)
        checksum.update(chunk)

        if len(data) > settings.upload_max_file_size_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=(
                    "Uploaded file is too large. "
                    f"Maximum allowed size is {settings.upload_max_file_size_mb} MB."
                ),
            )

    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    _validate_file_signature(
        content=bytes(data[:16]),
        content_type=content_type,
    )

    return ValidatedUpload(
        filename=filename,
        content_type=content_type,
        extension=ALLOWED_UPLOAD_MIME_TYPES[content_type],
        size_bytes=len(data),
        checksum_sha256=checksum.hexdigest(),
        content=bytes(data),
    )


def _validate_file_signature(content: bytes, content_type: str) -> None:
    is_valid = False

    if content_type == "application/pdf":
        is_valid = content.startswith(b"%PDF-")
    elif content_type == "image/jpeg":
        is_valid = content.startswith(b"\xff\xd8\xff")
    elif content_type == "image/png":
        is_valid = content.startswith(b"\x89PNG\r\n\x1a\n")

    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File content does not match declared MIME type.",
        )
