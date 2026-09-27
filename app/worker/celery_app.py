import logging
from celery import Celery
from app.core.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "diagnostic_booking",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    broker_connection_timeout=0.2,
    broker_connection_retry=False,
    broker_connection_max_retries=0,
    broker_connection_retry_on_startup=False,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)
