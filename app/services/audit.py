"""Shared helpers for calendar-related audit records."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog


def add_calendar_audit_log(
    *,
    db: Session,
    user_id: int,
    calendar_event_id: int,
    action: str,
    old_value: Any | None = None,
    new_value: Any | None = None,
) -> AuditLog:
    audit_log = AuditLog(
        calendar_event_id=calendar_event_id,
        user_id=user_id,
        action=action,
        old_value=old_value,
        new_value=new_value,
    )
    db.add(audit_log)
    return audit_log
