from pathlib import Path

import pytest

from app.core.config import settings
from app.services.storage import save_document_file


def test_save_document_file_writes_content_to_local_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "local_storage_path", str(tmp_path / "storage"))

    storage_key = "users/1/documents/1/original.pdf"
    content = b"%PDF-1.4\n%%EOF\n"

    saved_path = save_document_file(
        content=content,
        storage_key=storage_key,
    )

    assert saved_path == tmp_path / "storage/users/1/documents/1/original.pdf"
    assert saved_path.exists()
    assert saved_path.read_bytes() == content


def test_save_document_file_rejects_path_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "local_storage_path", str(tmp_path / "storage"))

    with pytest.raises(ValueError, match="Storage key resolves outside"):
        save_document_file(
            content=b"malicious",
            storage_key="../outside.pdf",
        )