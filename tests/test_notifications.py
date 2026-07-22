import pytest
from sqlalchemy.orm import Session

from app.models.notification import Notification
from app.models.user import User
from app.services.notifications import (
    NotificationNotFoundError,
    create_notification,
    list_notifications,
    mark_all_notifications_as_read,
    mark_notification_as_read,
    unread_notification_count,
)


def test_notifications_are_scoped_and_can_be_marked_read(
    db_session: Session,
    test_user: User,
) -> None:
    other = User(email="other@example.com", password_hash="hash")
    db_session.add(other)
    db_session.flush()
    first = create_notification(
        db=db_session,
        owner_id=test_user.id,
        title="First",
        body="First body",
    )
    create_notification(
        db=db_session,
        owner_id=other.id,
        title="Other",
        body="Other body",
    )
    db_session.commit()

    assert unread_notification_count(db=db_session, owner_id=test_user.id) == 1
    assert [item.title for item in list_notifications(db=db_session, owner_id=test_user.id)] == ["First"]

    read = mark_notification_as_read(
        db=db_session,
        owner_id=test_user.id,
        notification_id=first.id,
    )
    assert read.read_at is not None
    assert unread_notification_count(db=db_session, owner_id=test_user.id) == 0


def test_mark_all_only_marks_current_users_unread_notifications(
    db_session: Session,
    test_user: User,
) -> None:
    other = User(email="other@example.com", password_hash="hash")
    db_session.add(other)
    db_session.flush()
    for title in ("One", "Two"):
        create_notification(db=db_session, owner_id=test_user.id, title=title, body="Body")
    other_notification = create_notification(
        db=db_session,
        owner_id=other.id,
        title="Other",
        body="Body",
    )
    db_session.commit()

    assert mark_all_notifications_as_read(db=db_session, owner_id=test_user.id) == 2
    assert unread_notification_count(db=db_session, owner_id=test_user.id) == 0
    db_session.refresh(other_notification)
    assert other_notification.read_at is None


def test_mark_notification_rejects_a_notification_owned_by_another_user(
    db_session: Session,
    test_user: User,
) -> None:
    other = User(email="other@example.com", password_hash="hash")
    db_session.add(other)
    db_session.flush()
    notification = create_notification(
        db=db_session,
        owner_id=other.id,
        title="Private",
        body="Body",
    )
    db_session.commit()

    with pytest.raises(NotificationNotFoundError):
        mark_notification_as_read(
            db=db_session,
            owner_id=test_user.id,
            notification_id=notification.id,
        )
