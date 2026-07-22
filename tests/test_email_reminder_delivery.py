from app.core.config import Settings
from app.services.email_reminders import (
    EmailReminderMessage,
    FakeEmailReminderDeliverer,
    SMTPEmailReminderDeliverer,
    get_email_reminder_deliverer,
)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_secret_key": "test-secret-key-that-is-longer-than-thirty-two-characters",
        "database_url": "sqlite+pysqlite:///:memory:",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_email_reminders_are_disabled_by_default() -> None:
    configuration = _settings()

    assert configuration.email_reminder_delivery_available is False
    assert configuration.email_reminder_configuration_issues() == (
        "EMAIL_REMINDERS_ENABLED is false",
    )
    assert get_email_reminder_deliverer(configuration) is None


def test_enabled_email_reminders_require_safe_complete_configuration() -> None:
    configuration = _settings(email_reminders_enabled=True)

    assert configuration.email_reminder_delivery_available is False
    assert configuration.email_reminder_configuration_issues() == (
        "EMAIL_REMINDERS_FROM_ADDRESS",
        "EMAIL_REMINDERS_SMTP_HOST",
        "PUBLIC_APP_BASE_URL",
    )


def test_production_rejects_local_or_codespaces_public_email_url() -> None:
    configuration = _settings(
        app_env="production",
        app_debug=False,
        email_reminders_enabled=True,
        email_reminders_from_address="reminders@example.com",
        email_reminders_smtp_host="smtp.example.com",
        public_app_base_url="https://project-8000.app.github.dev",
    )

    assert configuration.email_reminder_delivery_available is False
    assert configuration.email_reminder_configuration_issues() == (
        "a non-local PUBLIC_APP_BASE_URL outside development",
    )


def test_fake_deliverer_records_message_without_network_access() -> None:
    deliverer = FakeEmailReminderDeliverer()
    message = EmailReminderMessage(
        recipient_email="person@example.com",
        subject="Reminder: Pay invoice",
        text_body="Invoice is due.",
        html_body="<p>Invoice is due.</p>",
        idempotency_key="reminder-10",
    )

    result = deliverer.deliver(message)

    assert deliverer.delivered == [message]
    assert result.provider_message_id == "fake-reminder-10"


def test_smtp_deliverer_is_constructed_only_when_configuration_is_complete() -> None:
    configuration = _settings(
        email_reminders_enabled=True,
        email_reminders_from_address="reminders@example.com",
        email_reminders_smtp_host="smtp.example.com",
        public_app_base_url="https://docsflow.example.com",
    )

    assert isinstance(get_email_reminder_deliverer(configuration), SMTPEmailReminderDeliverer)


def test_smtp_deliverer_submits_rendered_message_without_real_network(
    monkeypatch,
) -> None:
    submitted: list[object] = []

    class FakeSMTP:
        def __init__(self, *, host: str, port: int, timeout: int) -> None:
            assert (host, port, timeout) == ("smtp.example.com", 587, 20)

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def starttls(self) -> None:
            return None

        def login(self, username: str, password: str) -> None:
            assert (username, password) == ("smtp-user", "smtp-password")

        def send_message(self, message: object) -> None:
            submitted.append(message)

    monkeypatch.setattr("app.services.email_reminders.smtplib.SMTP", FakeSMTP)
    configuration = _settings(
        email_reminders_enabled=True,
        email_reminders_from_address="reminders@example.com",
        email_reminders_smtp_host="smtp.example.com",
        email_reminders_smtp_username="smtp-user",
        email_reminders_smtp_password="smtp-password",
        public_app_base_url="https://docsflow.example.com",
    )
    message = EmailReminderMessage(
        recipient_email="person@example.com",
        subject="Reminder: Pay invoice",
        text_body="Invoice is due.",
        html_body="<p>Invoice is due.</p>",
        idempotency_key="reminder-11",
    )

    result = SMTPEmailReminderDeliverer(configuration).deliver(message)

    assert result.provider_message_id == "<reminder-11@docsflow>"
    assert len(submitted) == 1
    email = submitted[0]
    assert email["To"] == "person@example.com"
    assert email["Message-ID"] == "<reminder-11@docsflow>"
