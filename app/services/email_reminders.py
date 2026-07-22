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
from html import escape
from typing import Protocol
from zoneinfo import ZoneInfo

from app.core.config import Settings, settings
from app.models.calendar_event import CalendarEvent
from app.models.event_reminder import EventReminder


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


def build_email_reminder_message(
    *,
    reminder: EventReminder,
    event: CalendarEvent,
    configuration: Settings = settings,
) -> EmailReminderMessage:
    """Render concise, escaped content without document text or extracted data."""
    recipient = (reminder.recipient_email or "").strip()
    if not recipient:
        raise ValueError("Email reminder has no recipient.")
    if not configuration.public_app_base_url:
        raise ValueError("Email reminder public URL is not configured.")

    when, timezone_name = _event_when(event)
    offset = _offset_label(reminder.offset_minutes)
    subject = f"Reminder: {event.title} — {offset}"
    event_url = f"{configuration.public_app_base_url}/calendar/events/{event.id}"
    text_body = (
        f"DocsFlow reminder\n\n{event.title}\nWhen: {when}\nTimezone: {timezone_name}"
        f"\nReminder: {offset}\n\nOpen event: {event_url}\n"
    )
    html_body = (
        "<!doctype html><html><body style=\"margin:0;background:#0b1220;color:#e6edf7;"
        "font-family:Arial,sans-serif\"><main style=\"max-width:560px;margin:24px auto;"
        "padding:24px;background:#172235;border:1px solid #304461;border-radius:14px\">"
        "<p style=\"margin:0 0 16px;color:#77e3c4;font-weight:700\">DOCSFLOW REMINDER</p>"
        f"<h1 style=\"font-size:22px;margin:0 0 18px\">{escape(event.title)}</h1>"
        "<section style=\"padding:16px;background:#0f1929;border-radius:10px\">"
        f"<p><strong>When:</strong> {escape(when)}</p>"
        f"<p><strong>Timezone:</strong> {escape(timezone_name)}</p>"
        f"<p><strong>Reminder:</strong> {escape(offset)}</p></section>"
        f"<p><a href=\"{escape(event_url, quote=True)}\" style=\"color:#77e3c4\">Open event in DocsFlow</a></p>"
        "<p style=\"color:#9aacc5;font-size:12px\">You receive this because email reminders were enabled for this event.</p>"
        "</main></body></html>"
    )
    return EmailReminderMessage(
        recipient_email=recipient,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        idempotency_key=f"event-reminder-{reminder.id}",
    )


def _event_when(event: CalendarEvent) -> tuple[str, str]:
    timezone_name = event.timezone or (event.owner.timezone if event.owner else "Europe/Berlin")
    if event.all_day:
        return (event.start_date.isoformat() if event.start_date else "Date unavailable", timezone_name)
    if event.start_at is None:
        return "Time unavailable", timezone_name
    local_start = event.start_at.astimezone(ZoneInfo(timezone_name))
    return local_start.strftime("%d %b %Y, %H:%M"), timezone_name


def _offset_label(offset_minutes: int) -> str:
    if offset_minutes == 0:
        return "at event time"
    if offset_minutes == 60:
        return "1 hour before"
    if offset_minutes == 24 * 60:
        return "1 day before"
    return f"{offset_minutes} minutes before"
