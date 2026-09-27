from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.models import Booking, BookingStatus, DiagnosticTest, User, UserRole


def create_booking(db: Session, *, payload, current_user: User) -> Booking:
    test = db.get(DiagnosticTest, payload.test_id)
    if not test:
        raise HTTPException(status_code=404, detail="Diagnostic test not found")
    booking = Booking(
        user_id=current_user.id,
        test_id=test.id,
        centre_id=test.centre_id,
        appointment_at=payload.appointment_at,
        amount=test.price,
        status=BookingStatus.PENDING,
    )
    db.add(booking)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if "uq_booking_slot" in str(exc.orig):
            raise HTTPException(status_code=409, detail="This appointment slot is already booked") from exc
        raise HTTPException(status_code=409, detail="Booking could not be created") from exc
    db.refresh(booking)
    return booking


def cancel_booking(db: Session, *, booking_id: str, current_user: User) -> Booking:
    booking = db.scalar(select(Booking).where(Booking.id == booking_id).with_for_update())
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    # Admins may cancel any booking; patients may only cancel their own.
    if current_user.role != UserRole.ADMIN and booking.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You cannot modify this booking")
    if booking.status not in {BookingStatus.PENDING, BookingStatus.CONFIRMED}:
        raise HTTPException(status_code=409, detail=f"Cannot cancel a {booking.status.value} booking")
    booking.status = BookingStatus.CANCELLED
    db.commit()
    db.refresh(booking)
    return booking
