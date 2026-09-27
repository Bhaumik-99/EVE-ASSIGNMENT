import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user, require_admin
from app.core.config import settings
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db, SessionLocal
from app.models import Booking, DiagnosticCentre, DiagnosticTest, PaymentStatus, User, UserRole
from app.schemas import (
    BookingCreate,
    BookingOut,
    CentreCreate,
    CentreOut,
    CentreUpdate,
    LoginRequest,
    PaymentOut,
    PaymentRequest,
    PaymentWebhook,
    TestCreate,
    TestOut,
    TestUpdate,
    TokenOut,
    UserCreate,
    UserOut,
)
from app.services.booking_service import cancel_booking as cancel_booking_service
from app.services.booking_service import create_booking as create_booking_service
from app.services.payment_service import process_mock_payment, process_webhook
from app.core.cache import cache
from app.worker.tasks import dispatch_task, send_booking_confirmation, process_webhook_retry

router = APIRouter()


def not_found(detail: str):
    raise HTTPException(status_code=404, detail=detail)


def verify_webhook_signature(raw_body: bytes, provided_signature: str | None) -> None:
    if not provided_signature:
        raise HTTPException(status_code=401, detail="Webhook signature required")
    expected = hmac.new(settings.webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, provided_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


@router.post("/auth/signup", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def signup(payload: UserCreate, db: Session = Depends(get_db)):
    email = payload.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="Email already registered")
    user = User(email=email, password_hash=hash_password(payload.password), full_name=payload.full_name)
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email already registered") from exc
    db.refresh(user)
    return user


@router.post("/auth/login", response_model=TokenOut)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return TokenOut(access_token=create_access_token(user.id))


@router.post("/centres", response_model=CentreOut, status_code=status.HTTP_201_CREATED)
def create_centre(payload: CentreCreate, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    centre = DiagnosticCentre(name=payload.name, location=payload.location)
    db.add(centre)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A centre with this name already exists at this location") from exc
    db.refresh(centre)
    cache.delete_pattern("centres:*")
    return centre


@router.patch("/centres/{centre_id}", response_model=CentreOut)
def update_centre(
    centre_id: str,
    payload: CentreUpdate,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    centre = db.scalar(
        select(DiagnosticCentre)
        .options(selectinload(DiagnosticCentre.tests))
        .where(DiagnosticCentre.id == centre_id)
    )
    if not centre:
        not_found("Diagnostic centre not found")
    target_name = payload.name if payload.name is not None else centre.name
    target_location = payload.location if payload.location is not None else centre.location
    conflict = db.scalar(
        select(DiagnosticCentre).where(
            DiagnosticCentre.name == target_name,
            DiagnosticCentre.location == target_location,
            DiagnosticCentre.id != centre_id,
        )
    )
    if conflict:
        raise HTTPException(
            status_code=409,
            detail="A centre with this name already exists at this location",
        )

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(centre, field, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A centre with this name already exists at this location",
        ) from exc
    db.refresh(centre)
    cache.delete_pattern("centres:*")
    cache.delete(f"centre:{centre_id}")
    return centre


@router.get("/centres", response_model=list[CentreOut])
def list_centres(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    cache_key = f"centres:list:{skip}:{limit}"
    cached = cache.get(cache_key)
    if cached is not None:
        return [CentreOut.model_validate(c) for c in cached]

    centres = db.scalars(
        select(DiagnosticCentre)
        .options(selectinload(DiagnosticCentre.tests))
        .order_by(DiagnosticCentre.name)
        .offset(skip)
        .limit(limit)
    ).all()
    cache.set(cache_key, [CentreOut.model_validate(c).model_dump(mode="json") for c in centres], ttl=120)
    return centres


@router.get("/centres/{centre_id}", response_model=CentreOut)
def get_centre(centre_id: str, db: Session = Depends(get_db)):
    cache_key = f"centre:{centre_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return CentreOut.model_validate(cached)

    centre = db.scalar(
        select(DiagnosticCentre)
        .options(selectinload(DiagnosticCentre.tests))
        .where(DiagnosticCentre.id == centre_id)
    )
    if not centre:
        not_found("Diagnostic centre not found")
    cache.set(cache_key, CentreOut.model_validate(centre).model_dump(mode="json"), ttl=120)
    return centre


@router.post("/centres/{centre_id}/tests", response_model=TestOut, status_code=status.HTTP_201_CREATED)
def create_test(
    centre_id: str,
    payload: TestCreate,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not db.get(DiagnosticCentre, centre_id):
        not_found("Diagnostic centre not found")
    test = DiagnosticTest(
        centre_id=centre_id,
        name=payload.name,
        description=payload.description,
        price=payload.price,
    )
    db.add(test)
    db.commit()
    db.refresh(test)
    cache.delete_pattern("centres:*")
    cache.delete(f"centre:{centre_id}:tests")
    return test


@router.patch("/centres/{centre_id}/tests/{test_id}", response_model=TestOut)
def update_test(
    centre_id: str,
    test_id: str,
    payload: TestUpdate,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    test = db.scalar(
        select(DiagnosticTest).where(
            DiagnosticTest.id == test_id,
            DiagnosticTest.centre_id == centre_id,
        )
    )
    if not test:
        not_found("Diagnostic test not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(test, field, value)
    db.commit()
    db.refresh(test)
    cache.delete_pattern("centres:*")
    cache.delete(f"centre:{centre_id}:tests")
    return test


@router.get("/centres/{centre_id}/tests", response_model=list[TestOut])
def list_tests(centre_id: str, db: Session = Depends(get_db)):
    if not db.get(DiagnosticCentre, centre_id):
        not_found("Diagnostic centre not found")
    cache_key = f"centre:{centre_id}:tests"
    cached = cache.get(cache_key)
    if cached is not None:
        return [TestOut.model_validate(t) for t in cached]

    tests = db.scalars(
        select(DiagnosticTest)
        .where(DiagnosticTest.centre_id == centre_id)
        .order_by(DiagnosticTest.name)
    ).all()
    cache.set(cache_key, [TestOut.model_validate(t).model_dump(mode="json") for t in tests], ttl=120)
    return tests


@router.post("/bookings", response_model=BookingOut, status_code=status.HTTP_201_CREATED)
def create_booking(
    payload: BookingCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return create_booking_service(db, payload=payload, current_user=current_user)


@router.get("/bookings", response_model=list[BookingOut])
def list_my_bookings(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List bookings. Admins see all bookings; patients see only their own."""
    stmt = select(Booking).order_by(Booking.created_at.desc()).offset(skip).limit(limit)
    if current_user.role.value != "ADMIN":
        stmt = stmt.where(Booking.user_id == current_user.id)
    return db.scalars(stmt).all()


@router.get("/bookings/{booking_id}", response_model=BookingOut)
def get_booking(booking_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Retrieve a booking by ID. Admins can access any booking; patients only their own."""
    booking = db.get(Booking, booking_id)
    if not booking:
        not_found("Booking not found")
    if current_user.role != UserRole.ADMIN and booking.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You cannot access this booking")
    return booking


@router.post("/bookings/{booking_id}/cancel", response_model=BookingOut)
def cancel_booking(booking_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Cancel a booking. Admins can cancel any booking; patients can only cancel their own."""
    return cancel_booking_service(db, booking_id=booking_id, current_user=current_user)


@router.post("/payments", response_model=PaymentOut, status_code=status.HTTP_201_CREATED)
def process_payment(
    payload: PaymentRequest,
    current_user: User = Depends(get_current_user),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    payment_status = payload.force_status or (PaymentStatus.SUCCESS if secrets.randbelow(2) else PaymentStatus.FAILED)
    if idempotency_key:
        idempotency_key = idempotency_key.strip() or None
        if idempotency_key and len(idempotency_key) > 100:
            raise HTTPException(status_code=422, detail="Idempotency-Key must be at most 100 characters")
    request_hash = None
    if idempotency_key:
        canonical = json.dumps(
            {
                "booking_id": payload.booking_id,
                "force_status": payload.force_status.value if payload.force_status else None,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        request_hash = hashlib.sha256(canonical).hexdigest()
    payment = process_mock_payment(
        db,
        booking_id=payload.booking_id,
        user_id=current_user.id,
        payment_status=PaymentStatus(payment_status),
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if payment.status == PaymentStatus.SUCCESS:
        dispatch_task(send_booking_confirmation, str(payload.booking_id))
    return payment


@router.post("/payments/webhook", response_model=PaymentOut)
async def payment_webhook(
    request: Request,
):
    # Each webhook call opens its own DB session so it is safe to call
    # process_webhook (a synchronous function) directly without run_in_threadpool.
    # Sharing the request-scoped `db` dependency across threads is not safe.
    raw_body = await request.body()
    verify_webhook_signature(raw_body, request.headers.get("X-Webhook-Signature"))
    try:
        payload_data = json.loads(raw_body)
        payload = PaymentWebhook.model_validate(payload_data)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid webhook payload") from exc
    with SessionLocal() as db:
        payment = process_webhook(
            db,
            event_id=payload.event_id,
            payment_id=payload.payment_id,
            booking_id=payload.booking_id,
            status=payload.status,
            amount=payload.amount,
        )
        # Eagerly load the payment data before the session closes
        payment_out = PaymentOut.model_validate(payment)
    if payment_out.status == PaymentStatus.SUCCESS:
        dispatch_task(send_booking_confirmation, str(payload.booking_id))
    return payment_out


@router.post("/payments/webhook/retry", status_code=status.HTTP_202_ACCEPTED)
async def payment_webhook_retry(
    request: Request,
):
    """Queues a webhook payload for resilient background execution with exponential backoff retries."""
    raw_body = await request.body()
    verify_webhook_signature(raw_body, request.headers.get("X-Webhook-Signature"))
    try:
        payload_data = json.loads(raw_body)
        payload = PaymentWebhook.model_validate(payload_data)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid webhook payload") from exc

    task_res = dispatch_task(
        process_webhook_retry,
        event_id=payload.event_id,
        payment_id=payload.payment_id,
        booking_id=payload.booking_id,
        status=payload.status.value,
        amount=str(payload.amount),
    )
    task_id = getattr(task_res, "id", "local-async")
    return {
        "status": "accepted",
        "message": "Webhook queued for processing with retry handling",
        "task_id": task_id,
    }

