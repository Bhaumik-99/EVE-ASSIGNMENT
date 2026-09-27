import logging
import time
from app.worker.celery_app import celery_app
from app.db.session import SessionLocal
from app.services.payment_service import process_webhook
from app.models import PaymentStatus, Booking, User
from decimal import Decimal

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=2)
def send_booking_confirmation(self, booking_id: str):
    """Background task to simulate dispatching email/SMS confirmation to patient."""
    logger.info("Starting background task: send_booking_confirmation for booking %s", booking_id)
    with SessionLocal() as db:
        booking = db.get(Booking, booking_id)
        if not booking:
            logger.warning("Booking %s not found for confirmation email", booking_id)
            return {"status": "not_found", "booking_id": booking_id}
        
        user = db.get(User, booking.user_id)
        email = user.email if user else "unknown"
        logger.info(
            "Booking confirmation dispatched to %s for booking %s (Amount: %s)",
            email,
            booking_id,
            booking.amount,
        )
        return {
            "status": "sent",
            "booking_id": booking_id,
            "recipient": email,
            "timestamp": time.time(),
        }


@celery_app.task(bind=True, max_retries=3, default_retry_delay=5)
def process_webhook_retry(
    self,
    event_id: str,
    payment_id: str,
    booking_id: str,
    status: str,
    amount: str,
):
    """
    Background retry task for transient webhook failures.
    Uses exponential backoff up to max_retries.
    """
    logger.info(
        "Processing webhook retry (Attempt %s/%s) for event %s",
        self.request.retries + 1,
        self.max_retries,
        event_id,
    )
    with SessionLocal() as db:
        try:
            payment = process_webhook(
                db=db,
                event_id=event_id,
                payment_id=payment_id,
                booking_id=booking_id,
                status=PaymentStatus(status),
                amount=Decimal(amount),
            )
            logger.info("Webhook retry succeeded for event %s, payment %s", event_id, payment.id)
            return {"status": "success", "event_id": event_id, "payment_id": payment.id}
        except Exception as exc:
            logger.warning("Webhook retry failed on attempt %s: %s", self.request.retries + 1, exc)
            if self.request.retries < self.max_retries:
                # Exponential backoff: 2s, 4s, 8s...
                countdown = 2 ** (self.request.retries + 1)
                raise self.retry(exc=exc, countdown=countdown)
            raise


def dispatch_task(task_func, *args, **kwargs):
    """
    Dispatches a task asynchronously via Celery worker.
    Falls back immediately to synchronous execution if the broker is unavailable.
    """
    try:
        return task_func.delay(*args, **kwargs)
    except Exception as exc:
        logger.info("Celery broker unavailable (%s); executing task synchronously", exc)
        return task_func(*args, **kwargs)



