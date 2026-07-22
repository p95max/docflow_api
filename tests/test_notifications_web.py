from sqlalchemy.orm import Session

from app.models.notification import Notification
from app.models.user import User


def _login(client, user: User) -> None:
    response = client.post(
        "/login",
        data={"email": user.email, "password": "strong-password", "csrf_token": client.cookies.get("docsflow_csrf_token")},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_notification_page_shows_unread_count_and_read_actions(client, db_session: Session, test_user: User) -> None:
    notification = Notification(owner_id=test_user.id, title="Pay invoice", body="Due today")
    db_session.add(notification)
    db_session.commit()
    _login(client, test_user)

    page = client.get("/notifications")
    assert page.status_code == 200
    assert "Pay invoice" in page.text
    assert "Mark all as read" in page.text
    assert f"/notifications/{notification.id}/read" in page.text
    assert "Notifications, 1 unread" in page.text

    response = client.post(f"/notifications/{notification.id}/read", follow_redirects=False)
    assert response.status_code == 303
    db_session.refresh(notification)
    assert notification.read_at is not None


def test_mark_all_notifications_as_read_from_page(client, db_session: Session, test_user: User) -> None:
    db_session.add_all(
        [
            Notification(owner_id=test_user.id, title="One", body="Body"),
            Notification(owner_id=test_user.id, title="Two", body="Body"),
        ]
    )
    db_session.commit()
    _login(client, test_user)

    response = client.post("/notifications/read-all", follow_redirects=False)
    assert response.status_code == 303
    assert all(item.read_at is not None for item in db_session.query(Notification).all())
