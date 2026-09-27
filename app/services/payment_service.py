from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Booking, BookingStatus, Payment, PaymentEvent, PaymentStatus


def _apply_payment_status(booking: Booking, status: PaymentStatus) -> None:
    if booking.status == BookingStatus.CANCELLED:
        raise HTTPException(status_code=409, detail="Cannot process payment for a cancelled booking")
    if booking.status in {BookingStatus.CONFIRMED, BookingStatus.FAILED}:
        raise HTTPException(
            status_code=409,
            detail=f"Booking is already in terminal state {booking.status.value}",
        )
    booking.status = BookingStatus.CONFIRMED if status == PaymentStatus.SUCCESS else BookingStatus.FAILED


def _payment_state_conflict(payment: Payment, status: PaymentStatus) -> bool:
    return payment.status != status


def process_mock_payment(
    db: Session,
    *,
    booking_id: str,
    user_id: str,
    payment_status: PaymentStatus,
    idempotency_key: str | None,
    request_hash: str | None,
) -> Payment:
    booking = db.scalar(select(Booking).where(Booking.id == booking_id).with_for_update())
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.user_id != user_id:
        raise HTTPException(status_code=403, detail="You cannot pay for this booking")
    if idempotency_key:
        by_key = db.scalar(select(Payment).where(Payment.idempotency_key == idempotency_key).with_for_update())
        if by_key:
            if by_key.booking_id != booking.id:
                raise HTTPException(status_code=409, detail="Idempotency key is already used for another booking")
            if by_key.idempotency_request_hash != request_hash:
                raise HTTPException(status_code=409, detail="Idempotency key was reused with a different request")
            return by_key

    existing = db.scalar(select(Payment).where(Payment.booking_id == booking.id).with_for_update())
    if existing:
        if existing.status != payment_status:
            raise HTTPException(
                status_code=409,
                detail="Booking already has a payment with a different status",
            )
        return existing

    if booking.status != BookingStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Payment not allowed for {booking.status.value} booking")

    payment = Payment(
        booking_id=booking.id,
        provider_payment_id=f"mock_pay_{uuid4().hex}",
        status=payment_status,
        amount=booking.amount,
        idempotency_key=idempotency_key,
        idempotency_request_hash=request_hash,
        processed_at=datetime.now(timezone.utc),
    )
    _apply_payment_status(booking, payment_status)
    db.add(payment)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        existing = db.scalar(select(Payment).where(Payment.booking_id == booking.id))
        if existing:
            if idempotency_key and existing.idempotency_key == idempotency_key and existing.idempotency_request_hash != request_hash:
                raise HTTPException(status_code=409, detail="Idempotency key was reused with a different request") from exc
            return existing
        raise HTTPException(status_code=409, detail="Payment could not be processed") from exc
    db.refresh(payment)
    return payment


def process_webhook(
    db: Session,
    *,
    event_id: str,
    payment_id: str,
    booking_id: str,
    status: PaymentStatus,
    amount,
) -> Payment:
    existing_event = db.scalar(select(PaymentEvent).where(PaymentEvent.event_id == event_id))
    if existing_event:
        if (
            existing_event.booking_id != booking_id
            or existing_event.provider_payment_id != payment_id
            or existing_event.status != status
            or existing_event.amount != amount
        ):
            raise HTTPException(status_code=409, detail="Event ID already used with different payload")
        payment = db.get(Payment, existing_event.payment_id)
        if not payment:
            raise HTTPException(status_code=409, detail="Webhook event references a missing payment")
        return payment

    booking = db.scalar(select(Booking).where(Booking.id == booking_id).with_for_update())
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.amount != amount:
        raise HTTPException(status_code=409, detail="Payment amount does not match booking amount")

    payment = db.scalar(select(Payment).where(Payment.booking_id == booking.id).with_for_update())
    if payment:
        if payment.provider_payment_id != payment_id:
            raise HTTPException(status_code=409, detail="Booking already has a different payment")
        if _payment_state_conflict(payment, status):
            raise HTTPException(status_code=409, detail="Payment is already finalized with a different status")
    else:
        _apply_payment_status(booking, status)
        payment = Payment(
            booking_id=booking.id,
            provider_payment_id=payment_id,
            status=status,
            amount=amount,
            processed_at=datetime.now(timezone.utc),
        )
        db.add(payment)
        db.flush()

    event = PaymentEvent(
        event_id=event_id,
        payment_id=payment.id,
        booking_id=booking.id,
        provider_payment_id=payment_id,
        status=status,
        amount=amount,
    )
    db.add(event)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        replay = db.scalar(select(PaymentEvent).where(PaymentEvent.event_id == event_id))
        if replay:
            if (replay.booking_id != booking_id or replay.provider_payment_id != payment_id or replay.status != status or replay.amount != amount):
                raise HTTPException(status_code=409, detail="Event ID already used with different payload") from exc
            payment = db.get(Payment, replay.payment_id)
            if payment:
                return payment
        raise HTTPException(status_code=409, detail="Webhook could not be processed") from exc
    db.refresh(payment)
    return payment
