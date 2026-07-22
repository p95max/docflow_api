"""Owner-scoped notification persistence and read-state operations."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.notification import Notification


class NotificationNotFoundError(LookupError):
    pass


def create_notification(
    *,
    db: Session,
    owner_id: int,
    title: str,
    body: str,
    event_id: int | None = None,
) -> Notification:
    """Stage a notification in the caller's transaction without committing it."""
    notification = Notification(
        owner_id=owner_id,
        event_id=event_id,
        title=title[:255],
        body=body,
    )
    db.add(notification)
    db.flush()
    return notification


def list_notifications(*, db: Session, owner_id: int, limit: int = 100) -> list[Notification]:
    return list(
        db.scalars(
            select(Notification)
            .where(Notification.owner_id == owner_id)
            .order_by(
                Notification.read_at.is_not(None).asc(),
                Notification.created_at.desc(),
                Notification.id.desc(),
            )
            .limit(limit)
        )
    )


def unread_notification_count(*, db: Session, owner_id: int) -> int:
    return int(
        db.scalar(
            select(func.count(Notification.id)).where(
                Notification.owner_id == owner_id,
                Notification.read_at.is_(None),
            )
        )
        or 0
    )


def mark_notification_as_read(*, db: Session, owner_id: int, notification_id: int) -> Notification:
    notification = db.scalar(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.owner_id == owner_id,
        )
    )
    if notification is None:
        raise NotificationNotFoundError("Notification not found.")
    if notification.read_at is None:
        notification.read_at = datetime.now(UTC)
        db.commit()
        db.refresh(notification)
    return notification


def mark_all_notifications_as_read(*, db: Session, owner_id: int) -> int:
    result = db.execute(
        update(Notification)
        .where(Notification.owner_id == owner_id, Notification.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )
    db.commit()
    return int(result.rowcount or 0)
