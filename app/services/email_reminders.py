"""Provider-neutral delivery primitives for optional email reminders.

The scheduler does not use this module until the email-delivery task is enabled.
Keeping transport selection here makes reminder lifecycle code independent from
SMTP and gives tests a no-network fake.
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr
from typing import Protocol

from app.core.config import Settings, settings


@dataclass(frozen=True)
class EmailReminderMessage:
    """Fully rendered content for one email provider submission."""

    recipient_email: str
    subject: str
    text_body: str
    html_body: str
    idempotency_key: str


@dataclass(frozen=True)
class EmailDeliveryResult:
    """Provider acceptance metadata safe to retain on a reminder row."""

    provider_message_id: str


class EmailReminderDeliverer(Protocol):
    """A provider-independent email delivery boundary."""

    def deliver(self, message: EmailReminderMessage) -> EmailDeliveryResult: ...


class SMTPEmailReminderDeliverer:
    """Minimal SMTP implementation using only the Python standard library."""

    def __init__(self, configuration: Settings = settings) -> None:
        issues = configuration.email_reminder_configuration_issues()
        if issues:
            raise ValueError(
                "Email reminders are unavailable: " + ", ".join(issues)
            )
        self._settings = configuration

    def deliver(self, message: EmailReminderMessage) -> EmailDeliveryResult:
        email = EmailMessage()
        email["From"] = formataddr(
            (self._settings.email_reminders_from_name, self._settings.email_reminders_from_address or "")
        )
        email["To"] = message.recipient_email
        email["Subject"] = message.subject
        # SMTP has no portable provider receipt. The deterministic Message-ID is
        # retained as the trace ID and becomes the idempotency key in task 4.
        provider_message_id = f"<{message.idempotency_key}@docsflow>"
        email["Message-ID"] = provider_message_id
        email.set_content(message.text_body)
        email.add_alternative(message.html_body, subtype="html")

        with smtplib.SMTP(
            host=self._settings.email_reminders_smtp_host,
            port=self._settings.email_reminders_smtp_port,
            timeout=20,
        ) as client:
            if self._settings.email_reminders_smtp_use_starttls:
                client.starttls()
            if self._settings.email_reminders_smtp_username:
                client.login(
                    self._settings.email_reminders_smtp_username,
                    self._settings.email_reminders_smtp_password or "",
                )
            client.send_message(email)
        return EmailDeliveryResult(provider_message_id=provider_message_id)


@dataclass
class FakeEmailReminderDeliverer:
    """No-network deliverer for deterministic unit and integration tests."""

    delivered: list[EmailReminderMessage] = field(default_factory=list)

    def deliver(self, message: EmailReminderMessage) -> EmailDeliveryResult:
        self.delivered.append(message)
        return EmailDeliveryResult(provider_message_id=f"fake-{message.idempotency_key}")


def get_email_reminder_deliverer(
    configuration: Settings = settings,
) -> EmailReminderDeliverer | None:
    """Return the configured provider, or ``None`` when delivery is disabled."""
    if not configuration.email_reminder_delivery_available:
        return None
    if configuration.email_reminders_provider == "smtp":
        return SMTPEmailReminderDeliverer(configuration)
    raise ValueError("Unsupported email reminder provider")
