import os
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

os.environ["APP_SECRET_KEY"] = "test-secret-key-with-at-least-32-bytes-long"
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["CORS_ORIGINS"] = '["http://testserver"]'
os.environ["UPLOAD_MAX_FILE_SIZE_MB"] = "1"
os.environ["UPLOAD_RATE_LIMIT_REQUESTS"] = "100"
os.environ["UPLOAD_RATE_LIMIT_WINDOW_SECONDS"] = "60"
os.environ["LOCAL_STORAGE_PATH"] = "storage-test"

from app.core.config import settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Document, User  # noqa: F401, E402
from app.services.security import create_access_token  # noqa: E402
from app.services.uploads import _upload_rate_limit_state  # noqa: E402
from app.services.users import create_user  # noqa: E402


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    TestingSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    Base.metadata.create_all(bind=engine)

    with TestingSessionLocal() as session:
        yield session

    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture
def client(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    storage_path = tmp_path / "storage"

    monkeypatch.setattr(settings, "local_storage_path", str(storage_path))
    monkeypatch.setattr(settings, "upload_max_file_size_mb", 1)
    monkeypatch.setattr(settings, "upload_rate_limit_requests", 100)
    monkeypatch.setattr(settings, "upload_rate_limit_window_seconds", 60)

    _upload_rate_limit_state.clear()

    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    _upload_rate_limit_state.clear()


@pytest.fixture
def test_user(db_session: Session) -> User:
    return create_user(
        db=db_session,
        email="user@example.com",
        password="strong-password",
    )


@pytest.fixture
def auth_headers(test_user: User) -> dict[str, str]:
    token = create_access_token(subject=str(test_user.id))
    return {"Authorization": f"Bearer {token}"}