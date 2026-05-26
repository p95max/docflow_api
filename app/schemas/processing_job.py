from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.processing_job import ProcessingJobStatus, ProcessingOperationType


class ProcessingJobRead(BaseModel):
    id: int
    document_id: int
    operation_type: ProcessingOperationType
    status: ProcessingJobStatus
    celery_task_id: str | None
    attempts: int
    max_retries: int
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)