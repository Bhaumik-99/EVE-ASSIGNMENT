import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone


def auth(client, email="user@example.com", password="password123"):
    r = client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": password, "full_name": "Test User"},
    )
    assert r.status_code == 201
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def admin_auth(client):
    r = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "Admin12345!"},
    )
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def setup_test(client, headers, suffix=""):
    centre = client.post(
        "/api/v1/centres",
        headers=headers,
        json={"name": f"City Diagnostics{suffix}", "location": "Delhi"},
    )
    assert centre.status_code == 201
    centre_id = centre.json()["id"]
    test = client.post(
        f"/api/v1/centres/{centre_id}/tests",
        headers=headers,
        json={"name": "CBC", "price": "500.00"},
    )
    assert test.status_code == 201
    return centre_id, test.json()["id"]


def webhook(client, payload, secret="test-webhook"):
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/api/v1/payments/webhook",
        content=body,
        headers={"X-Webhook-Signature": signature, "Content-Type": "application/json"},
    )


def make_booking(client, user_headers, admin_headers=None, days=1, suffix=""):
    admin_headers = admin_headers or admin_auth(client)
    _, test_id = setup_test(client, admin_headers, suffix=suffix)
    appointment = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    booking = client.post(
        "/api/v1/bookings",
        headers=user_headers,
        json={"test_id": test_id, "appointment_at": appointment},
    )
    assert booking.status_code == 201
    return booking.json()


def test_signup_login_and_protected_route(client):
    headers = auth(client)
    assert client.get("/api/v1/bookings", headers=headers).status_code == 200
    assert client.get("/api/v1/bookings").status_code == 401


def test_non_admin_cannot_manage_centres(client):
    headers = auth(client)
    assert client.post(
        "/api/v1/centres", headers=headers, json={"name": "City Diagnostics", "location": "Delhi"}
    ).status_code == 403


def test_admin_can_manage_centres_and_tests(client):
    headers = admin_auth(client)
    centre_id, test_id = setup_test(client, headers)
    updated = client.patch(
        f"/api/v1/centres/{centre_id}",
        headers=headers,
        json={"location": "New Delhi"},
    )
    assert updated.status_code == 200
    assert updated.json()["location"] == "New Delhi"

    updated_test = client.patch(
        f"/api/v1/centres/{centre_id}/tests/{test_id}",
        headers=headers,
        json={"price": "550.00"},
    )
    assert updated_test.status_code == 200
    assert updated_test.json()["price"] == "550.00"


def test_booking_snapshots_test_price(client):
    headers = auth(client)
    admin = admin_auth(client)
    centre_id, test_id = setup_test(client, admin)
    appointment = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    booking = client.post(
        "/api/v1/bookings",
        headers=headers,
        json={"test_id": test_id, "appointment_at": appointment},
    )
    assert booking.status_code == 201
    assert booking.json()["amount"] == "500.00"
    client.patch(f"/api/v1/centres/{centre_id}/tests/{test_id}", headers=admin, json={"price": "650.00"})
    stored = client.get(f"/api/v1/bookings/{booking.json()['id']}", headers=headers)
    assert stored.json()["amount"] == "500.00"


def test_booking_and_success_payment(client):
    headers = auth(client)
    booking = make_booking(client, headers)
    payment = client.post(
        "/api/v1/payments",
        headers={**headers, "Idempotency-Key": "pay-request-1"},
        json={"booking_id": booking["id"], "force_status": "SUCCESS"},
    )
    assert payment.status_code == 201
    assert payment.json()["status"] == "SUCCESS"
    assert client.get(f"/api/v1/bookings/{booking['id']}", headers=headers).json()["status"] == "CONFIRMED"


def test_payment_request_is_idempotent(client):
    headers = auth(client)
    booking = make_booking(client, headers, days=5)
    request = {"booking_id": booking["id"], "force_status": "SUCCESS"}
    first = client.post("/api/v1/payments", headers={**headers, "Idempotency-Key": "same-key"}, json=request)
    second = client.post("/api/v1/payments", headers={**headers, "Idempotency-Key": "same-key"}, json=request)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


def test_idempotency_key_cannot_be_reused_for_different_payload(client):
    headers = auth(client)
    booking = make_booking(client, headers, days=18)
    key = "same-key-different-request"
    first = client.post(
        "/api/v1/payments",
        headers={**headers, "Idempotency-Key": key},
        json={"booking_id": booking["id"], "force_status": "SUCCESS"},
    )
    second = client.post(
        "/api/v1/payments",
        headers={**headers, "Idempotency-Key": key},
        json={"booking_id": booking["id"], "force_status": "FAILED"},
    )
    assert first.status_code == 201
    assert second.status_code == 409


def test_idempotency_key_cannot_be_reused_for_another_booking(client):
    headers = auth(client)
    first_booking = make_booking(client, headers, days=16, suffix=" One")
    second_booking = make_booking(client, headers, days=17, suffix=" Two")
    assert client.post(
        "/api/v1/payments",
        headers={**headers, "Idempotency-Key": "cross-booking-key"},
        json={"booking_id": first_booking["id"], "force_status": "SUCCESS"},
    ).status_code == 201
    assert client.post(
        "/api/v1/payments",
        headers={**headers, "Idempotency-Key": "cross-booking-key"},
        json={"booking_id": second_booking["id"], "force_status": "SUCCESS"},
    ).status_code == 409


def test_failed_payment_moves_booking_to_failed(client):
    headers = auth(client)
    booking = make_booking(client, headers, days=6)
    payment = client.post(
        "/api/v1/payments",
        headers=headers,
        json={"booking_id": booking["id"], "force_status": "FAILED"},
    )
    assert payment.status_code == 201
    assert payment.json()["status"] == "FAILED"
    assert client.get(f"/api/v1/bookings/{booking['id']}", headers=headers).json()["status"] == "FAILED"


def test_webhook_requires_hmac_signature(client):
    headers = auth(client)
    booking = make_booking(client, headers, days=7)
    payload = {
        "event_id": "evt_sig",
        "payment_id": "pay_sig",
        "booking_id": booking["id"],
        "status": "SUCCESS",
        "amount": "500.00",
    }
    assert client.post("/api/v1/payments/webhook", json=payload).status_code == 401
    assert webhook(client, payload, secret="wrong-secret").status_code == 401


def test_webhook_is_idempotent_and_replay_safe(client):
    headers = auth(client)
    booking = make_booking(client, headers, days=8)
    payload = {
        "event_id": "evt_123",
        "payment_id": "pay_123",
        "booking_id": booking["id"],
        "status": "SUCCESS",
        "amount": "500.00",
    }
    first = webhook(client, payload)
    second = webhook(client, payload)
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]

    # A distinct event for the same finalized provider payment is accepted,
    # while the payment state remains unchanged.
    later_event = {**payload, "event_id": "evt_124"}
    third = webhook(client, later_event)
    assert third.status_code == 200
    assert third.json()["id"] == first.json()["id"]
    assert client.get(f"/api/v1/bookings/{booking['id']}", headers=headers).json()["status"] == "CONFIRMED"


def test_same_event_id_with_different_payload_is_rejected(client):
    headers = auth(client)
    booking = make_booking(client, headers, days=9)
    payload = {
        "event_id": "evt_conflict",
        "payment_id": "pay_conflict",
        "booking_id": booking["id"],
        "status": "SUCCESS",
        "amount": "500.00",
    }
    assert webhook(client, payload).status_code == 200
    changed = {**payload, "amount": "499.00"}
    assert webhook(client, changed).status_code == 409


def test_conflicting_status_cannot_corrupt_confirmed_booking(client):
    headers = auth(client)
    booking = make_booking(client, headers, days=10)
    success = {
        "event_id": "evt_success",
        "payment_id": "pay_same",
        "booking_id": booking["id"],
        "status": "SUCCESS",
        "amount": "500.00",
    }
    assert webhook(client, success).status_code == 200
    conflicting = {**success, "event_id": "evt_failed", "status": "FAILED"}
    assert webhook(client, conflicting).status_code == 409
    stored = client.get(f"/api/v1/bookings/{booking['id']}", headers=headers)
    assert stored.json()["status"] == "CONFIRMED"


def test_webhook_amount_mismatch_is_rejected(client):
    headers = auth(client)
    booking = make_booking(client, headers, days=11)
    payload = {
        "event_id": "evt_amount",
        "payment_id": "pay_amount",
        "booking_id": booking["id"],
        "status": "SUCCESS",
        "amount": "501.00",
    }
    assert webhook(client, payload).status_code == 409


def test_cancelled_booking_cannot_be_paid_or_resurrected(client):
    headers = auth(client)
    booking = make_booking(client, headers, days=12)
    booking_id = booking["id"]
    assert client.post(f"/api/v1/bookings/{booking_id}/cancel", headers=headers).status_code == 200
    payment = client.post(
        "/api/v1/payments", headers=headers, json={"booking_id": booking_id, "force_status": "SUCCESS"}
    )
    assert payment.status_code == 409
    payload = {
        "event_id": "evt_cancelled",
        "payment_id": "pay_cancelled",
        "booking_id": booking_id,
        "status": "SUCCESS",
        "amount": "500.00",
    }
    assert webhook(client, payload).status_code == 409
    assert client.get(f"/api/v1/bookings/{booking_id}", headers=headers).json()["status"] == "CANCELLED"


def test_cannot_access_or_pay_for_another_users_booking(client):
    user1 = auth(client, "one@example.com")
    booking = make_booking(client, user1, days=13)
    user2 = auth(client, "two@example.com")
    assert client.get(f"/api/v1/bookings/{booking['id']}", headers=user2).status_code == 403
    assert client.post(f"/api/v1/bookings/{booking['id']}/cancel", headers=user2).status_code == 403
    assert client.post(
        "/api/v1/payments", headers=user2, json={"booking_id": booking["id"], "force_status": "SUCCESS"}
    ).status_code == 403


def test_duplicate_appointment_is_rejected(client):
    headers = auth(client)
    admin = admin_auth(client)
    _, test_id = setup_test(client, admin)
    appointment = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
    body = {"test_id": test_id, "appointment_at": appointment}
    assert client.post("/api/v1/bookings", headers=headers, json=body).status_code == 201
    assert client.post("/api/v1/bookings", headers=headers, json=body).status_code == 409


def test_invalid_and_repeated_cancellation_are_handled(client):
    headers = auth(client)
    assert client.get("/api/v1/bookings/not-a-real-id", headers=headers).status_code == 404
    booking = make_booking(client, headers, days=15)
    booking_id = booking["id"]
    assert client.post(f"/api/v1/bookings/{booking_id}/cancel", headers=headers).status_code == 200
    assert client.post(f"/api/v1/bookings/{booking_id}/cancel", headers=headers).status_code == 409


def test_request_id_is_returned(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers.get("X-Request-ID")


def test_health_and_readiness(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").status_code == 200


def test_auth_rate_limit_can_be_configured(client, monkeypatch):
    from app.core.config import settings
    from app.core.observability import rate_limiter
    rate_limiter._hits.clear()
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 1)
    first = client.post("/api/v1/auth/login", json={"email": "missing@example.com", "password": "bad"})
    second = client.post("/api/v1/auth/login", json={"email": "missing@example.com", "password": "bad"})
    assert first.status_code == 401
    assert second.status_code == 429
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 30)


def test_existing_payment_cannot_be_reprocessed_with_conflicting_status(client):
    headers = auth(client)
    booking = make_booking(client, headers, days=22)
    first = client.post(
        "/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "force_status": "SUCCESS"}
    )
    assert first.status_code == 201
    second = client.post(
        "/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "force_status": "FAILED"}
    )
    assert second.status_code == 409


# ---------------------------------------------------------------------------
# Input validation tests
# ---------------------------------------------------------------------------

def test_signup_rejects_blank_full_name(client):
    r = client.post(
        "/api/v1/auth/signup",
        json={"email": "blank@example.com", "password": "password123", "full_name": "   "},
    )
    assert r.status_code == 422


def test_signup_rejects_short_password(client):
    r = client.post(
        "/api/v1/auth/signup",
        json={"email": "short@example.com", "password": "abc", "full_name": "Alice"},
    )
    assert r.status_code == 422


def test_create_test_rejects_blank_name(client):
    admin = admin_auth(client)
    centre = client.post(
        "/api/v1/centres",
        headers=admin,
        json={"name": "Test Centre", "location": "Delhi"},
    )
    assert centre.status_code == 201
    r = client.post(
        f"/api/v1/centres/{centre.json()['id']}/tests",
        headers=admin,
        json={"name": "  ", "price": "100.00"},
    )
    assert r.status_code == 422


def test_booking_rejects_past_appointment(client):
    headers = auth(client)
    admin = admin_auth(client)
    _, test_id = setup_test(client, admin)
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    r = client.post(
        "/api/v1/bookings",
        headers=headers,
        json={"test_id": test_id, "appointment_at": past},
    )
    assert r.status_code == 422


def test_booking_rejects_naive_datetime(client):
    headers = auth(client)
    admin = admin_auth(client)
    _, test_id = setup_test(client, admin)
    naive = "2030-01-01T10:00:00"  # no timezone
    r = client.post(
        "/api/v1/bookings",
        headers=headers,
        json={"test_id": test_id, "appointment_at": naive},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Pagination tests
# ---------------------------------------------------------------------------

def test_bookings_list_pagination(client):
    headers = auth(client)
    admin = admin_auth(client)
    # Create 3 bookings with uniquely-named centres on different days.
    for i, day in enumerate(range(23, 26)):
        make_booking(client, headers, admin_headers=admin, days=day, suffix=f" P{i}")
    page1 = client.get("/api/v1/bookings?skip=0&limit=2", headers=headers)
    assert page1.status_code == 200
    assert len(page1.json()) == 2
    page2 = client.get("/api/v1/bookings?skip=2&limit=2", headers=headers)
    assert page2.status_code == 200
    assert len(page2.json()) == 1
    # Ensure the IDs across pages are distinct
    ids_p1 = {b["id"] for b in page1.json()}
    ids_p2 = {b["id"] for b in page2.json()}
    assert ids_p1.isdisjoint(ids_p2)


def test_centres_list_pagination(client):
    admin = admin_auth(client)
    for i in range(3):
        client.post(
            "/api/v1/centres",
            headers=admin,
            json={"name": f"Centre {i}", "location": f"City {i}"},
        )
    page = client.get("/api/v1/centres?skip=0&limit=2")
    assert page.status_code == 200
    assert len(page.json()) == 2


# ---------------------------------------------------------------------------
# Duplicate centre tests
# ---------------------------------------------------------------------------

def test_duplicate_centre_is_rejected(client):
    admin = admin_auth(client)
    payload = {"name": "Unique Centre", "location": "Mumbai"}
    assert client.post("/api/v1/centres", headers=admin, json=payload).status_code == 201
    assert client.post("/api/v1/centres", headers=admin, json=payload).status_code == 409


# ---------------------------------------------------------------------------
# Public endpoint access tests
# ---------------------------------------------------------------------------

def test_unauthenticated_user_can_list_centres(client):
    assert client.get("/api/v1/centres").status_code == 200


def test_unauthenticated_user_cannot_list_bookings(client):
    assert client.get("/api/v1/bookings").status_code == 401

