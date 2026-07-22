from datetime import UTC, date, datetime

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.main import app
from app.models.calendar_event import CalendarEvent, CalendarEventStatus, CalendarEventType
from app.models.event_reminder import EventReminder, EventReminderChannel, EventReminderStatus
from app.models.user import User
from app.web import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
from app.services.security import create_access_token


def _login(client: TestClient, user: User) -> None:
    token = create_access_token(subject=str(user.id))
    client.cookies.set(SESSION_COOKIE_NAME, token)


def test_account_email_switch_cancels_email_only_reminders(
    db_session: Session,
    test_user: User,
) -> None:
    event = CalendarEvent(
        owner_id=test_user.id,
        title="Pay invoice",
        event_type=CalendarEventType.payment_due,
        status=CalendarEventStatus.confirmed,
        start_date=date(2026, 8, 1),
        source_evidence={},
    )
    email = EventReminder(
        event=event,
        channel=EventReminderChannel.email,
        recipient_email=test_user.email,
        offset_minutes=60,
        scheduled_for=datetime(2026, 8, 1, tzinfo=UTC),
    )
    in_app = EventReminder(
        event=event,
        channel=EventReminderChannel.in_app,
        offset_minutes=0,
        scheduled_for=datetime(2026, 8, 1, 1, tzinfo=UTC),
    )
    db_session.add_all([email, in_app])
    db_session.commit()
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            csrf_token = "email-reminders-settings-csrf-token"
            client.cookies.set(CSRF_COOKIE_NAME, csrf_token)
            client.headers["X-CSRF-Token"] = csrf_token
            _login(client, test_user)
            response = client.post(
                "/settings/email-reminders",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == status.HTTP_303_SEE_OTHER
    db_session.refresh(test_user)
    db_session.refresh(email)
    db_session.refresh(in_app)
    assert test_user.email_reminders_enabled is False
    assert email.status == EventReminderStatus.cancelled
    assert in_app.status == EventReminderStatus.pending
    assert db_session.scalar(select(EventReminder).where(EventReminder.id == email.id)) is email
