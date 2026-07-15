import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "production",
        "app_debug": False,
        "init_test_user": False,
        "app_secret_key": "a-secure-random-production-secret-over-32-bytes",
        "database_url": "sqlite+pysqlite:///:memory:",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_app_secret_key_requires_at_least_32_characters() -> None:
    with pytest.raises(ValidationError, match="at least 32 characters"):
        _production_settings(app_secret_key="too-short")


def test_production_rejects_placeholder_secret() -> None:
    with pytest.raises(ValidationError, match="must not use a placeholder"):
        _production_settings(
            app_secret_key="change-me-in-production-with-more-than-32-bytes"
        )


def test_production_rejects_debug_and_test_user() -> None:
    with pytest.raises(ValidationError, match="APP_DEBUG must be false"):
        _production_settings(app_debug=True)

    with pytest.raises(ValidationError, match="INIT_TEST_USER must be false"):
        _production_settings(init_test_user=True)
