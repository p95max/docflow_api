from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.openai_usage_log import OpenAIUsageLog
from app.models.user import User
from app.web import _get_ai_usage_summary


def test_ai_usage_summary_counts_recent_owner_usage_by_model(
    db_session: Session,
    test_user: User,
) -> None:
    db_session.add_all(
        [
            OpenAIUsageLog(
                owner_id=test_user.id,
                operation="document_ai_extraction",
                model="gpt-4o-mini",
                input_tokens=14,
                output_tokens=6,
            ),
            OpenAIUsageLog(
                owner_id=test_user.id,
                operation="knowledge_answer",
                model="gpt-5.6-terra",
                total_tokens=40,
            ),
            OpenAIUsageLog(
                owner_id=test_user.id,
                operation="old_usage",
                model="gpt-4o-mini",
                total_tokens=999,
                created_at=datetime.now(UTC) - timedelta(days=2),
            ),
        ]
    )
    db_session.commit()
    db_session.refresh(test_user)

    summary = _get_ai_usage_summary(test_user)

    assert summary is not None
    assert summary["used_tokens"] == 60
    assert summary["recorded_operations"] == 2
    assert summary["nav_model"] == "gpt-5.6-terra"
    assert summary["models"] == [
        {"name": "gpt-5.6-terra", "tokens": 40, "operations": 1},
        {"name": "gpt-4o-mini", "tokens": 20, "operations": 1},
    ]
