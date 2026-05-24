from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.document import DocumentStatus, ProcessingMode


class DocumentRead(BaseModel):
    id: int
    original_filename: str
    status: DocumentStatus
    processing_mode: ProcessingMode
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)