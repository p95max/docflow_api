import os
import uuid
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base


@pytest.fixture
def pgvector_session() -> Generator[Session, None, None]:
    """Provide an isolated schema when TEST_POSTGRESQL_URL is configured."""
    database_url = os.getenv("TEST_POSTGRESQL_URL")
    if not database_url:
        pytest.skip("Set TEST_POSTGRESQL_URL to run PostgreSQL/pgvector tests.")

    engine = create_engine(database_url, pool_pre_ping=True)
    schema_name = f"test_pgvector_{uuid.uuid4().hex}"
    connection: Connection | None = None
    session: Session | None = None

    try:
        with engine.begin() as setup_connection:
            setup_connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            setup_connection.execute(text(f"CREATE SCHEMA {schema_name}"))

        connection = engine.connect().execution_options(
            schema_translate_map={None: schema_name}
        )
        connection.execute(text(f"SET search_path TO {schema_name}, public"))
        Base.metadata.create_all(bind=connection)
        connection.commit()

        SessionLocal = sessionmaker(
            bind=connection,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
        session = SessionLocal()
        yield session

    finally:
        if session is not None:
            session.close()
        if connection is not None:
            connection.execute(text("SET search_path TO public"))
            connection.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE"))
            connection.commit()
            connection.close()
        engine.dispose()
