import hashlib
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from app.core.config import settings
from app.models.user import User

ALLOWED_UPLOAD_MIME_TYPES = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/png": "png",
}

UPLOAD_CHUNK_SIZE = 1024 * 1024

_upload_rate_limit_state: dict[int, deque[datetime]] = defaultdict(deque)


@dataclass(frozen=True)
class ValidatedUpload:
    filename: str
    content_type: str
    extension: str
    size_bytes: int
    checksum_sha256: str
    content: bytes


def enforce_upload_rate_limit(current_user: User) -> None:
    """Apply a simple in-memory per-user rate limit for document uploads."""
    now = datetime.now(UTC)
    window = timedelta(seconds=settings.upload_rate_limit_window_seconds)
    bucket = _upload_rate_limit_state[current_user.id]

    while bucket and bucket[0] <= now - window:
        bucket.popleft()

    if len(bucket) >= settings.upload_rate_limit_requests:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Upload rate limit exceeded. Please try again later.",
        )

    bucket.append(now)


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