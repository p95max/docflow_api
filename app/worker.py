from celery import Celery
from celery.schedules import crontab

from app import models as _models  # noqa: F401
from app.core.config import settings

celery_app = Celery(
    "docsflow",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.tasks.documents",
        "app.tasks.backups",
        "app.tasks.knowledge",
        "app.tasks.reminders",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Europe/Berlin",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=True,
)

celery_app.conf.beat_schedule = {
    "calendar-due-reminders": {
        "task": "calendar.schedule_due_reminders",
        "schedule": crontab(minute="*/5"),
    }
}

if settings.automatic_backups_enabled:
    celery_app.conf.beat_schedule.update(
        {
            "weekly-automatic-recovery-backups": {
                "task": "backups.schedule_automatic_backups",
                "schedule": crontab(
                    minute=0,
                    hour=settings.automatic_backup_hour,
                    day_of_week=settings.automatic_backup_weekday,
                ),
            }
        }
    )
