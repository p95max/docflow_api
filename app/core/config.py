import json

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "local"
    app_debug: bool = True
    app_secret_key: str = Field(min_length=16)
    access_token_expire_minutes: int = 30
    database_url: str
    cors_origins: list[str] = ["http://localhost:8000"]

    upload_max_file_size_mb: int = 10
    upload_rate_limit_requests: int = 10
    upload_rate_limit_window_seconds: int = 60

    local_storage_path: str = "storage"

    celery_broker_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/1"
    celery_task_always_eager: bool = False

    document_processing_soft_time_limit_seconds: int = 60
    document_processing_hard_time_limit_seconds: int = 90
    document_processing_max_retries: int = 3
    document_processing_retry_delay_seconds: int = 10

    local_ocr_languages: str = "eng+deu"

    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    openai_request_timeout_seconds: int = 45
    openai_max_input_chars: int = 12000

    @property
    def upload_max_file_size_bytes(self) -> int:
        return self.upload_max_file_size_mb * 1024 * 1024

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, list):
            return value

        value = value.strip()

        if value.startswith("["):
            parsed = json.loads(value)
            if not isinstance(parsed, list):
                raise ValueError("CORS_ORIGINS must be a list or comma-separated string")
            return [str(origin).strip() for origin in parsed if str(origin).strip()]

        return [origin.strip() for origin in value.split(",") if origin.strip()]


settings = Settings()