from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.document import DocumentStatus, ProcessingMode


class DocumentRead(BaseModel):
    id: int
    original_filename: str
    status: DocumentStatus
    processing_mode: ProcessingMode
    content_type: str | None
    file_size_bytes: int | None
    checksum_sha256: str | None

    document_type: str | None
    ai_extracted_data: dict[str, Any] | None
    ai_extraction_model: str | None
    ai_extraction_completed_at: datetime | None

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)