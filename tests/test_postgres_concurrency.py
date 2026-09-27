import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models import Booking, BookingStatus, DiagnosticCentre, DiagnosticTest, User
from app.services.booking_service import create_booking
from app.schemas import BookingCreate

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.integration


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not configured")
def test_postgres_concurrent_booking_requests_are_serialized():
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    with SessionLocal() as db:
        user = User(email=f"concurrency-{os.getpid()}@example.com", password_hash="test", full_name="Concurrency User")
        centre = DiagnosticCentre(name="Concurrency Centre", location="Delhi")
        test = DiagnosticTest(name="CBC", price=500, centre=centre)
        db.add_all([user, centre, test])
        db.commit()
        db.refresh(user); db.refresh(test)
        user_id, test_id = user.id, test.id

    appointment = datetime.now(timezone.utc) + timedelta(days=10)

    def attempt():
        with SessionLocal() as db:
            try:
                booking = create_booking(
                    db,
                    payload=BookingCreate(test_id=test_id, appointment_at=appointment),
                    current_user=db.get(User, user_id),
                )
                return booking.id
            except Exception:
                db.rollback()
                return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: attempt(), range(2)))

    with SessionLocal() as db:
        bookings = db.scalars(select(Booking).where(Booking.user_id == user_id)).all()
        assert len(bookings) == 1
        assert sum(result is not None for result in results) == 1
        assert bookings[0].status == BookingStatus.PENDING

    engine.dispose()

from app.models import Payment, PaymentStatus
from app.services.payment_service import process_mock_payment, process_webhook


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not configured")
def test_postgres_concurrent_payment_requests_share_one_payment():
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    with SessionLocal() as db:
        user = User(email=f"payment-{os.getpid()}@example.com", password_hash="test", full_name="Payment User")
        centre = DiagnosticCentre(name="Payment Centre", location="Delhi")
        test = DiagnosticTest(name="CBC", price=500, centre=centre)
        db.add_all([user, centre, test]); db.commit(); db.refresh(user); db.refresh(test)
        appointment = datetime.now(timezone.utc) + timedelta(days=20)
        booking = create_booking(db, payload=BookingCreate(test_id=test.id, appointment_at=appointment), current_user=user)
        booking_id, user_id = booking.id, user.id

    def attempt():
        with SessionLocal() as db:
            try:
                payment = process_mock_payment(
                    db,
                    booking_id=booking_id,
                    user_id=user_id,
                    payment_status=PaymentStatus.SUCCESS,
                    idempotency_key="concurrent-payment-key",
                    request_hash="same-request-hash",
                )
                return payment.id
            except Exception:
                db.rollback()
                return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: attempt(), range(2)))

    with SessionLocal() as db:
        payments = db.scalars(select(Payment).where(Payment.booking_id == booking_id)).all()
        assert len(payments) == 1
        assert results[0] == results[1] == payments[0].id
        assert payments[0].status == PaymentStatus.SUCCESS
    engine.dispose()


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not configured")
def test_postgres_concurrent_duplicate_webhooks_are_idempotent():
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    with SessionLocal() as db:
        user = User(email=f"webhook-{os.getpid()}@example.com", password_hash="test", full_name="Webhook User")
        centre = DiagnosticCentre(name="Webhook Centre", location="Delhi")
        test = DiagnosticTest(name="CBC", price=500, centre=centre)
        db.add_all([user, centre, test]); db.commit(); db.refresh(user); db.refresh(test)
        appointment = datetime.now(timezone.utc) + timedelta(days=21)
        booking = create_booking(db, payload=BookingCreate(test_id=test.id, appointment_at=appointment), current_user=user)
        booking_id = booking.id

    def attempt():
        with SessionLocal() as db:
            try:
                payment = process_webhook(
                    db,
                    event_id="concurrent-event-1",
                    payment_id="concurrent-provider-payment",
                    booking_id=booking_id,
                    status=PaymentStatus.SUCCESS,
                    amount=500,
                )
                return payment.id
            except Exception:
                db.rollback()
                return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: attempt(), range(2)))

    with SessionLocal() as db:
        payments = db.scalars(select(Payment).where(Payment.booking_id == booking_id)).all()
        assert len(payments) == 1
        assert all(result == payments[0].id for result in results)
        assert payments[0].status == PaymentStatus.SUCCESS
    engine.dispose()
