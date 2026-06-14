from pathlib import Path

from app.core.config import settings
from app.models.document import Document


def build_document_storage_key(document: Document, extension: str) -> str:
    """Build an internal object key for an uploaded document file."""
    return f"users/{document.owner_id}/documents/{document.id}/original.{extension}"


def save_document_file(content: bytes, storage_key: str) -> Path:
    """Save file content to local storage and return the absolute file path."""
    target_path = get_document_file_path(storage_key)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(content)
    return target_path


def delete_document_file(storage_key: str | None) -> None:
    """Delete a stored file if it exists."""
    if not storage_key:
        return

    file_path = get_document_file_path(storage_key)
    file_path.unlink(missing_ok=True)


def get_document_file_path(storage_key: str) -> Path:
    """Resolve a storage key without allowing path traversal."""
    storage_root = Path(settings.local_storage_path).resolve()
    target_path = (storage_root / storage_key).resolve()

    if not target_path.is_relative_to(storage_root):
        raise ValueError(
            "Storage key resolves outside of the configured storage root"
        )

    return target_path