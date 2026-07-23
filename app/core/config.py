import json
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "local"
    app_debug: bool = True
    app_secret_key: str = Field(min_length=32)
    access_token_expire_minutes: int = 30
    remember_me_token_expire_days: int = Field(default=30, ge=1, le=90)
    database_url: str
    cors_origins: list[str] = ["http://localhost:8000"]

    init_test_user: bool = False
    test_user_email: str = "m@m.com"
    test_user_password: str = "12345678"

    upload_max_file_size_mb: int = 10
    upload_rate_limit_requests: int = 10
    upload_rate_limit_window_seconds: int = 60

    rate_limit_enabled: bool = True
    rate_limit_redis_url: str = "redis://redis:6379/2"
    login_rate_limit_requests: int = Field(default=10, ge=1)
    login_rate_limit_window_seconds: int = Field(default=300, ge=1)
    registration_rate_limit_requests: int = Field(default=5, ge=1)
    registration_rate_limit_window_seconds: int = Field(default=3600, ge=1)
    semantic_search_rate_limit_requests: int = Field(default=30, ge=1)
    semantic_search_rate_limit_window_seconds: int = Field(default=60, ge=1)
    knowledge_question_rate_limit_requests: int = Field(default=10, ge=1)
    knowledge_question_rate_limit_window_seconds: int = Field(default=60, ge=1)
    calendar_write_rate_limit_requests: int = Field(default=60, ge=1)
    calendar_write_rate_limit_window_seconds: int = Field(default=60, ge=1)
    openai_daily_request_quota: int = Field(default=200, ge=1)
    openai_daily_token_quota: int = Field(default=500_000, ge=1)

    local_storage_path: str = "storage"

    celery_broker_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/1"
    celery_task_always_eager: bool = False

    reminder_max_attempts: int = Field(default=3, ge=1, le=20)
    reminder_retry_base_seconds: int = Field(default=60, ge=1, le=86_400)
    reminder_scheduler_batch_size: int = Field(default=100, ge=1, le=1_000)
    calendar_beat_health_max_age_seconds: int = Field(default=900, ge=60, le=86_400)
    reminder_backlog_alert_threshold: int = Field(default=50, ge=1, le=100_000)

    # Email reminders are opt-in at deployment level and remain unavailable until
    # all required SMTP settings are present. Values are deliberately kept as
    # plain configuration fields; they are never persisted with reminders.
    email_reminders_enabled: bool = False
    email_reminders_provider: Literal["smtp"] = "smtp"
    email_reminders_from_address: str | None = None
    email_reminders_from_name: str = "DocsFlow Reminders"
    email_reminders_smtp_host: str | None = None
    email_reminders_smtp_port: int = Field(default=587, ge=1, le=65_535)
    email_reminders_smtp_username: str | None = None
    email_reminders_smtp_password: str | None = None
    email_reminders_smtp_use_starttls: bool = True
    public_app_base_url: str | None = None

    document_processing_soft_time_limit_seconds: int = 60
    document_processing_hard_time_limit_seconds: int = 90
    document_processing_max_retries: int = 3
    document_processing_retry_delay_seconds: int = 10
    document_preview_token_expire_minutes: int = 10

    backup_soft_time_limit_seconds: int = 120
    backup_hard_time_limit_seconds: int = 180
    backup_max_retained: int = Field(default=5, ge=1, le=100)
    automatic_backups_enabled: bool = True
    automatic_backup_weekday: str = "sun"
    automatic_backup_hour: int = Field(default=3, ge=0, le=23)
    backup_master_key: str | None = None
    backup_restore_max_file_size_mb: int = Field(default=25, ge=1)
    backup_restore_max_decompressed_size_mb: int = Field(default=100, ge=1)
    backup_restore_max_documents: int = Field(default=2000, ge=1)
    backup_restore_max_raw_text_chars: int = Field(default=2_000_000, ge=1)
    backup_allow_legacy_restore: bool = False

    google_drive_client_id: str | None = None
    google_drive_client_secret: str | None = None
    google_drive_redirect_uri: str | None = None
    google_drive_refresh_token: str | None = None  # Deprecated; ignored by OAuth flow.
    google_drive_token_encryption_key: str | None = None
    google_drive_token_previous_encryption_keys: str = ""
    google_drive_folder_name: str = "docflow_backup"
    google_drive_timeout_seconds: int = 60
    google_oauth_state_expire_minutes: int = 10

    local_ocr_languages: str = "eng+deu"

    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    openai_validation_fallback_model: str | None = None
    openai_request_timeout_seconds: int = 45
    openai_max_input_chars: int = 12000
    openai_embedding_model: str = "text-embedding-3-small"
    openai_embedding_dimensions: int = 1536
    openai_rag_model: str
    openai_rag_reasoning_effort: Literal[
        "none", "low", "medium", "high", "xhigh", "max"
    ] = "high"
    openai_rag_max_context_chars: int = 24000
    openai_rag_max_output_tokens: int = 800
    knowledge_retrieval_limit: int = 8
    knowledge_min_similarity: float = 0.25
    knowledge_history_message_limit: int = 8
    knowledge_history_token_budget: int = 1600
    knowledge_enabled: bool = True
    document_indexing_soft_time_limit_seconds: int = 120
    document_indexing_hard_time_limit_seconds: int = 180
    document_indexing_batch_size: int = 64
    document_chunk_size_tokens: int = 700
    document_chunk_overlap_tokens: int = 100

    @property
    def upload_max_file_size_bytes(self) -> int:
        return self.upload_max_file_size_mb * 1024 * 1024

    @property
    def backup_restore_max_file_size_bytes(self) -> int:
        return self.backup_restore_max_file_size_mb * 1024 * 1024

    @property
    def backup_restore_max_decompressed_size_bytes(self) -> int:
        return self.backup_restore_max_decompressed_size_mb * 1024 * 1024

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
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

    @field_validator("automatic_backup_weekday")
    @classmethod
    def validate_automatic_backup_weekday(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}:
            raise ValueError(
                "AUTOMATIC_BACKUP_WEEKDAY must be one of mon, tue, wed, thu, fri, sat, sun"
            )
        return normalized

    @field_validator("public_app_base_url")
    @classmethod
    def validate_public_app_base_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip().rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("PUBLIC_APP_BASE_URL must be an absolute HTTP(S) URL")
        if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ValueError("PUBLIC_APP_BASE_URL must not contain a path, query, or fragment")
        return normalized

    def email_reminder_configuration_issues(self) -> tuple[str, ...]:
        """Return safe, non-secret reasons why email delivery is unavailable."""
        if not self.email_reminders_enabled:
            return ("EMAIL_REMINDERS_ENABLED is false",)

        missing: list[str] = []
        if not self.email_reminders_from_address:
            missing.append("EMAIL_REMINDERS_FROM_ADDRESS")
        if not self.email_reminders_smtp_host:
            missing.append("EMAIL_REMINDERS_SMTP_HOST")
        if not self.public_app_base_url:
            missing.append("PUBLIC_APP_BASE_URL")
        username_set = bool(self.email_reminders_smtp_username)
        password_set = bool(self.email_reminders_smtp_password)
        if username_set != password_set:
            missing.append("both SMTP username and password")

        if self.public_app_base_url and self.app_env.casefold() not in {"local", "test"}:
            hostname = (urlparse(self.public_app_base_url).hostname or "").casefold()
            if hostname in {"localhost", "127.0.0.1", "::1"} or hostname.endswith(
                ".app.github.dev"
            ):
                missing.append("a non-local PUBLIC_APP_BASE_URL outside development")
        return tuple(missing)

    @property
    def email_reminder_delivery_available(self) -> bool:
        return not self.email_reminder_configuration_issues()

    @model_validator(mode="after")
    def validate_deployment_security(self) -> "Settings":
        if self.app_env.casefold() not in {"local", "test"}:
            if self.app_debug:
                raise ValueError("APP_DEBUG must be false outside local development")
            if self.init_test_user:
                raise ValueError("INIT_TEST_USER must be false outside local development")
            normalized_secret = self.app_secret_key.casefold()
            if "change-me" in normalized_secret or "development" in normalized_secret:
                raise ValueError(
                    "APP_SECRET_KEY must not use a placeholder outside local development"
                )
        return self


settings = Settings()
