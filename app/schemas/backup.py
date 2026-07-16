from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.backup_job import BackupJobStatus


class BackupJobRead(BaseModel):
    id: int
    owner_id: int
    status: BackupJobStatus
    celery_task_id: str | None
    is_automatic: bool
    drive_folder_id: str | None
    drive_file_id: str | None
    drive_file_name: str | None
    drive_web_view_link: str | None
    content_type: str
    compressed_size_bytes: int | None
    checksum_sha256: str | None
    recovery_key_id: str | None
    record_counts: dict[str, Any] | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
