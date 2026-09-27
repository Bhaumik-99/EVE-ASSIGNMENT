# Diagnostic Booking Service

A production-grade FastAPI backend for diagnostic centre discovery, authenticated bookings, simulated payments, and idempotent payment webhooks.

---

## Table of Contents
1. [Engineering Highlights](#engineering-highlights)
2. [Architecture Overview](#architecture-overview)
3. [How to Run the Project Locally](#how-to-run-the-project-locally)
   - [Prerequisites](#prerequisites)
   - [Option A: Docker Compose (Recommended)](#option-a-docker-compose-recommended)
   - [Option B: Local Python Virtualenv (Without Docker)](#option-b-local-python-virtualenv-without-docker)
   - [Running the Test Suite](#running-the-test-suite)
4. [API Endpoints and Example Requests](#api-endpoints-and-example-requests)
   - [1. Authentication](#1-authentication)
   - [2. Diagnostic Centres & Tests](#2-diagnostic-centres--tests)
   - [3. Bookings](#3-bookings)
   - [4. Simulated Payments](#4-simulated-payments)
   - [5. Payment Webhooks](#5-payment-webhooks)
   - [6. Health & Readiness](#6-health--readiness)
5. [Database / Schema Design](#database--schema-design)
   - [Entity-Relationship Diagram (ERD)](#entity-relationship-diagram-erd)
   - [Schema Details & Constraints](#schema-details--constraints)
   - [Database-Enforced Guarantees](#database-enforced-guarantees)
6. [State Machine & Execution Flow](#state-machine--execution-flow)
   - [Booking Lifecycle State Diagram](#booking-lifecycle-state-diagram)
   - [Payment & Webhook Sequence Flow](#payment--webhook-sequence-flow)
7. [Important Assumptions You Made](#important-assumptions-you-made)
8. [Edge Cases Handled](#edge-cases-handled)
9. [What You Would Improve If You Had More Time](#what-you-would-improve-if-you-had-more-time)

---

## Engineering Highlights

- **FastAPI & Pydantic V2**: Clean route layering, strong typing, and auto-generated Swagger UI / OpenAPI schemas.
- **PostgreSQL & SQLAlchemy 2**: Fully declarative typed models (`Mapped[...]`), connection pooling with `pool_pre_ping=True`, and managed migrations via Alembic.
- **Enterprise Security**: Argon2id password hashing via `pwdlib`, asymmetric/HS256 JWT tokens, and strict production startup secrets validation.
- **Role-Based Access Control (RBAC)**: Public patient registration, authenticated patient self-service, and protected `ADMIN` catalogue management.
- **Double-Booking Prevention**: Database-level composite unique constraint `(centre_id, test_id, appointment_at)` coupled with atomic transaction isolation.
- **Stripe-Style Payment Idempotency**: Support for client `Idempotency-Key` headers paired with canonical request SHA-256 digests.
- **Webhook Ledger & Replay Safety**: Dedicated `payment_events` immutable ledger table with unique provider `event_id` and HMAC-SHA256 request signature verification.
- **Terminal State Protection**: Explicit state guards preventing regressions (e.g. `CONFIRMED` cannot regress to `FAILED`, and `CANCELLED` bookings cannot be resurrected).
- **Observability & Resilience**: Structured JSON logging, `X-Request-ID` correlation tracking, in-memory rate limiting with `Retry-After` headers, and Kubernetes `/health` & `/ready` probes.

---

## Architecture Overview

### System Architecture Overview

```mermaid
graph TB
    subgraph Clients["Clients & External Consumers"]
        PAT["👤 Patient Client<br/>(Web / Mobile App)"]
        ADM["🛡️ Admin Client<br/>(Staff Portal)"]
        PGW["💳 Payment Gateway<br/>(Webhook Dispatcher)"]
    end

    subgraph Ingress["Security & Ingress Layer"]
        CORS["CORS Middleware<br/>(Restricted Origins)"]
        RL["Rate Limiter<br/>(In-Memory / Token Bucket)"]
        RID["Request Tracing<br/>(X-Request-ID Header)"]
        LOG["Structured Logger<br/>(JSON with Secret Redaction)"]
        HMAC["HMAC-SHA256 Verifier<br/>(X-Webhook-Signature)"]
    end

    subgraph API["FastAPI Application (/api/v1)"]
        AUTH["Auth Router<br/>/auth/signup & /auth/login"]
        CTR["Centres Router<br/>/centres & /centres/{id}/tests"]
        BKG["Bookings Router<br/>/bookings & /bookings/{id}/cancel"]
        PAY["Payments Router<br/>/payments & /payments/webhook"]
        HLT["Observability<br/>/health & /ready"]
    end

    subgraph Core["Core Services & Security"]
        SEC["Security Module<br/>Argon2id + JWT (HS256)"]
        CFG["Config Guard<br/>Pydantic Settings + Production Checks"]
    end

    subgraph Services["Domain Service Layer"]
        BS["Booking Service<br/>• Slot Conflict Prevention<br/>• Price Snapshotting<br/>• Atomic Cancellation"]
        PS["Payment Service<br/>• Idempotency-Key & Hash Matching<br/>• Ledger Event Recording<br/>• Terminal State Protection"]
    end

    subgraph Storage["PostgreSQL 16 Database"]
        U[("users<br/>(id, email, password_hash, role)")]
        DC[("diagnostic_centres<br/>(id, name, location)")]
        DT[("diagnostic_tests<br/>(id, centre_id, name, price)")]
        BK[("bookings<br/>(id, user_id, centre_id, slot, status)")]
        PM[("payments<br/>(id, booking_id, idempotency_key, status)")]
        PE[("payment_events<br/>(id, event_id, status, payload_hash)")]
    end

    PAT -->|"HTTPS / REST (JWT)"| CORS
    ADM -->|"HTTPS / REST (JWT Admin)"| CORS
    PGW -->|"POST /payments/webhook"| CORS

    CORS --> RL --> RID --> LOG

    LOG --> AUTH
    LOG --> CTR
    LOG --> BKG
    LOG --> PAY
    LOG --> HLT

    AUTH -.->|"Hash & Verify"| SEC
    PAY -.->|"Verify Signature"| HMAC

    AUTH --> U
    CTR --> DC
    CTR --> DT
    BKG --> BS
    PAY --> PS

    BS --> BK
    BS --> DT
    BS --> DC
    PS --> PM
    PS --> PE
    PS --> BK
```

---

## How to Run the Project Locally

### Prerequisites
- **Docker & Docker Compose** (Recommended) OR **Python 3.11+ / 3.12+ / 3.13** & **PostgreSQL 15+**

---

### Option A: Docker Compose (Recommended)

1. **Clone and enter the directory**:
   ```bash
   cd diagnostic-booking-service
   ```

2. **Configure environment variables**:
   ```bash
   cp .env.example .env
   ```
   *(For development/demo, defaults in `.env.example` work out of the box).*

3. **Start services with Docker Compose**:
   ```bash
   docker compose up --build
   ```
   The API container automatically applies database migrations (`alembic upgrade head`) before starting Uvicorn.

4. **Verify running service**:
   - API Base: `http://localhost:8000`
   - Swagger Documentation: `http://localhost:8000/docs`
   - Health Probe: `http://localhost:8000/health`
   - Readiness Probe: `http://localhost:8000/ready`

---

### Option B: Local Python Virtualenv (Without Docker)

1. **Create and activate a virtual environment**:
   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # macOS / Linux:
   source .venv/bin/activate
   ```

2. **Install dependencies**:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

3. **Set up `.env`**:
   ```bash
   cp .env.example .env
   ```
   Update `DATABASE_URL` in `.env` to point to your local PostgreSQL instance:
   ```env
   DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/diagnostic_booking
   ```

4. **Run database migrations**:
   ```bash
   alembic upgrade head
   ```

5. **Start the development server**:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

---

### Running the Test Suite

1. **Run Unit & API Integration Tests**:
   ```bash
   pytest
   ```
   *Runs 32 test scenarios against isolated in-memory storage covering auth, catalog, booking, idempotency, and webhooks.*

2. **Run PostgreSQL Concurrency & Race-Condition Tests**:
   Ensure a local PostgreSQL instance is running, then execute:
   ```bash
   TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/diagnostic_booking pytest -m integration
   ```
   *Validates slot contention and concurrent payment locks under multi-threaded execution.*

---

## API Endpoints and Example Requests

### Interactive API Documentation
- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`
- **OpenAPI Schema**: `http://localhost:8000/openapi.json`

---

### 1. Authentication

#### User Signup
- **Endpoint**: `POST /api/v1/auth/signup`
- **Access**: Public
- **Request Body**:
  ```json
  {
    "email": "patient@example.com",
    "password": "Password123!",
    "full_name": "Jane Doe"
  }
  ```
- **Example cURL**:
  ```bash
  curl -X POST "http://localhost:8000/api/v1/auth/signup" \
    -H "Content-Type: application/json" \
    -d '{"email":"patient@example.com","password":"Password123!","full_name":"Jane Doe"}'
  ```
- **Response (`201 Created`)**:
  ```json
  {
    "id": "e4b6c31a-7b24-4f93-875f-2c49a377d612",
    "email": "patient@example.com",
    "full_name": "Jane Doe",
    "role": "PATIENT",
    "created_at": "2026-09-27T10:00:00Z"
  }
  ```

#### User Login
- **Endpoint**: `POST /api/v1/auth/login`
- **Access**: Public
- **Request Body**:
  ```json
  {
    "email": "patient@example.com",
    "password": "Password123!"
  }
  ```
- **Response (`200 OK`)**:
  ```json
  {
    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "token_type": "bearer"
  }
  ```
> **Note**: Pass the returned token in subsequent requests as:  
> `Authorization: Bearer <access_token>`

---

### 2. Diagnostic Centres & Tests

#### List Diagnostic Centres (Paginated)
- **Endpoint**: `GET /api/v1/centres?skip=0&limit=20`
- **Access**: Public
- **Response (`200 OK`)**:
  ```json
  [
    {
      "id": "c1a2b3c4-d5e6-7f80-9a1b-2c3d4e5f6071",
      "name": "Apollo Diagnostics Central",
      "location": "Indiranagar, Bengaluru",
      "created_at": "2026-09-27T08:00:00Z"
    }
  ]
  ```

#### Get Centre by ID
- **Endpoint**: `GET /api/v1/centres/{centre_id}`
- **Access**: Public

#### List Tests Available at a Centre
- **Endpoint**: `GET /api/v1/centres/{centre_id}/tests`
- **Access**: Public
- **Response (`200 OK`)**:
  ```json
  [
    {
      "id": "d2e3f4a5-b6c7-8091-a2b3-c4d5e6f70812",
      "centre_id": "c1a2b3c4-d5e6-7f80-9a1b-2c3d4e5f6071",
      "name": "Complete Blood Count (CBC)",
      "description": "Comprehensive 24-parameter automated hemogram",
      "price": "450.00",
      "created_at": "2026-09-27T08:30:00Z"
    }
  ]
  ```

#### Create Diagnostic Centre (Admin Only)
- **Endpoint**: `POST /api/v1/centres`
- **Access**: `ADMIN` role required
- **Request Body**:
  ```json
  {
    "name": "Max Healthcare Labs",
    "location": "Saket, New Delhi"
  }
  ```

#### Create Diagnostic Test (Admin Only)
- **Endpoint**: `POST /api/v1/centres/{centre_id}/tests`
- **Access**: `ADMIN` role required
- **Request Body**:
  ```json
  {
    "name": "Lipid Profile",
    "description": "Cholesterol and triglyceride assessment",
    "price": "850.00"
  }
  ```

---

### 3. Bookings

#### Create a Booking
- **Endpoint**: `POST /api/v1/bookings`
- **Access**: Authenticated (`PATIENT`)
- **Headers**: `Authorization: Bearer <token>`
- **Request Body**:
  ```json
  {
    "test_id": "d2e3f4a5-b6c7-8091-a2b3-c4d5e6f70812",
    "appointment_at": "2026-10-15T09:30:00+05:30"
  }
  ```
  *(Timestamp must include timezone offset and be in the future; normalized to UTC).*
- **Example cURL**:
  ```bash
  curl -X POST "http://localhost:8000/api/v1/bookings" \
    -H "Authorization: Bearer <token>" \
    -H "Content-Type: application/json" \
    -d '{"test_id":"d2e3f4a5-b6c7-8091-a2b3-c4d5e6f70812","appointment_at":"2026-10-15T09:30:00+05:30"}'
  ```
- **Response (`201 Created`)**:
  ```json
  {
    "id": "b0a1b2c3-d4e5-6f70-8a9b-0c1d2e3f4a5b",
    "user_id": "e4b6c31a-7b24-4f93-875f-2c49a377d612",
    "centre_id": "c1a2b3c4-d5e6-7f80-9a1b-2c3d4e5f6071",
    "test_id": "d2e3f4a5-b6c7-8091-a2b3-c4d5e6f70812",
    "appointment_at": "2026-10-15T04:00:00Z",
    "amount": "450.00",
    "status": "PENDING",
    "created_at": "2026-09-27T10:15:00Z"
  }
  ```

#### List User's Bookings (Paginated)
- **Endpoint**: `GET /api/v1/bookings?skip=0&limit=20`
- **Access**: Authenticated (`PATIENT` sees only their own; `ADMIN` sees all)

#### Cancel a Booking
- **Endpoint**: `POST /api/v1/bookings/{booking_id}/cancel`
- **Access**: Authenticated owner or `ADMIN`
- **Response (`200 OK`)**:
  ```json
  {
    "id": "b0a1b2c3-d4e5-6f70-8a9b-0c1d2e3f4a5b",
    "status": "CANCELLED"
  }
  ```

---

### 4. Simulated Payments

#### Process Mock Payment (with Idempotency Key)
- **Endpoint**: `POST /api/v1/payments`
- **Access**: Authenticated booking owner
- **Headers**:
  - `Authorization: Bearer <token>`
  - `Idempotency-Key: checkout-trans-9921` *(Optional but recommended)*
- **Request Body**:
  ```json
  {
    "booking_id": "b0a1b2c3-d4e5-6f70-8a9b-0c1d2e3f4a5b",
    "force_status": "SUCCESS"
  }
  ```
  *(Note: `force_status` is optional; if omitted, the mock gateway non-deterministically returns `SUCCESS` or `FAILED`).*
- **Response (`200 OK`)**:
  ```json
  {
    "id": "p9a8b7c6-d5e4-3f21-0a9b-8c7d6e5f4a3b",
    "booking_id": "b0a1b2c3-d4e5-6f70-8a9b-0c1d2e3f4a5b",
    "provider_payment_id": "pay_mock_3f820a4b",
    "status": "SUCCESS",
    "amount": "450.00",
    "created_at": "2026-09-27T10:20:00Z"
  }
  ```

---

### 5. Payment Webhooks

#### Webhook Ingestion
- **Endpoint**: `POST /api/v1/payments/webhook`
- **Access**: Gateway Provider (Requires HMAC-SHA256 Signature Header)
- **Headers**:
  - `X-Webhook-Signature: <hex_encoded_hmac_sha256>`
- **Request Body**:
  ```json
  {
    "event_id": "evt_gateway_99812",
    "payment_id": "pay_gateway_3341",
    "booking_id": "b0a1b2c3-d4e5-6f70-8a9b-0c1d2e3f4a5b",
    "status": "SUCCESS",
    "amount": "450.00"
  }
  ```
- **How to Compute Webhook Signature (Python)**:
  ```python
  import hmac
  import hashlib

  secret = b"change-me-webhook-secret"
  raw_body = b'{"event_id":"evt_99","payment_id":"pay_99","booking_id":"...","status":"SUCCESS","amount":"450.00"}'
  signature = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
  # Include header: X-Webhook-Signature: <signature>
  ```
- **Response (`200 OK`)**:
  ```json
  {
    "id": "p9a8b7c6-d5e4-3f21-0a9b-8c7d6e5f4a3b",
    "booking_id": "b0a1b2c3-d4e5-6f70-8a9b-0c1d2e3f4a5b",
    "provider_payment_id": "pay_gateway_3341",
    "status": "SUCCESS",
    "amount": "450.00"
  }
  ```

---

### 6. Health & Readiness

- **`GET /health`**: Returns `{"status": "ok"}` for container liveness.
- **`GET /ready`**: Executes `SELECT 1` on PostgreSQL to confirm active DB connectivity.

---

## Database / Schema Design

### Entity-Relationship Diagram (ERD)

```mermaid
erDiagram
    USERS ||--o{ BOOKINGS : "places"
    DIAGNOSTIC_CENTRES ||--o{ DIAGNOSTIC_TESTS : "offers"
    DIAGNOSTIC_CENTRES ||--o{ BOOKINGS : "hosts"
    DIAGNOSTIC_TESTS ||--o{ BOOKINGS : "scheduled_for"
    BOOKINGS ||--o| PAYMENTS : "has_single"
    PAYMENTS ||--o{ PAYMENT_EVENTS : "records_ledger"
    BOOKINGS ||--o{ PAYMENT_EVENTS : "associated_with"

    USERS {
        uuid id PK
        string email UK "Unique patient/admin email"
        string password_hash "Argon2id digest"
        string full_name
        string role "ADMIN or PATIENT"
        timestamp created_at
    }

    DIAGNOSTIC_CENTRES {
        uuid id PK
        string name "Composite UK (name, location)"
        string location "City / Address"
        timestamp created_at
    }

    DIAGNOSTIC_TESTS {
        uuid id PK
        uuid centre_id FK
        string name "Test title (e.g. CBC, MRI)"
        string description
        numeric price "Numeric(10, 2)"
        timestamp created_at
    }

    BOOKINGS {
        uuid id PK
        uuid user_id FK
        uuid centre_id FK
        uuid test_id FK
        timestamp appointment_at "Composite UK (centre, test, appointment)"
        numeric amount "Historical price snapshot"
        enum status "PENDING, CONFIRMED, FAILED, CANCELLED"
        timestamp created_at
    }

    PAYMENTS {
        uuid id PK
        uuid booking_id FK,UK "1:1 Booking-to-Payment guarantee"
        string provider_payment_id UK
        enum status "PENDING, SUCCESS, FAILED"
        numeric amount
        string idempotency_key UK "Client-supplied idempotency key"
        string idempotency_request_hash "SHA-256 payload digest"
        timestamp created_at
    }

    PAYMENT_EVENTS {
        uuid id PK
        string event_id UK "Unique provider event identifier"
        uuid payment_id FK
        uuid booking_id FK
        string provider_payment_id
        enum status "PENDING, SUCCESS, FAILED"
        numeric amount
        timestamp received_at
    }
```

---

### Schema Details & Constraints

| Table | Column | Type | Constraints / Purpose |
|---|---|---|---|
| **users** | `id` | UUID (String 36) | Primary Key |
| | `email` | String(255) | Unique, Indexed, Case-normalized |
| | `password_hash` | String(255) | Argon2id cryptographic hash |
| | `role` | String(32) | Enum: `ADMIN`, `PATIENT` |
| **diagnostic_centres** | `id` | UUID (String 36) | Primary Key |
| | `name`, `location` | String(255) | `UniqueConstraint("name", "location")` prevents duplicates |
| **diagnostic_tests** | `id` | UUID (String 36) | Primary Key |
| | `centre_id` | UUID FK | References `diagnostic_centres.id` (`ondelete="RESTRICT"`) |
| | `price` | Numeric(10, 2) | Stored with exact precision (no float rounding errors) |
| **bookings** | `id` | UUID (String 36) | Primary Key |
| | `user_id`, `centre_id`, `test_id` | UUID FKs | References User, Centre, and Test tables |
| | `appointment_at` | DateTime(timezone=True) | UTC timestamp; `UniqueConstraint("centre_id", "test_id", "appointment_at")` |
| | `amount` | Numeric(10, 2) | **Snapshot of test price** at moment of booking creation |
| | `status` | Enum | `PENDING`, `CONFIRMED`, `FAILED`, `CANCELLED` |
| **payments** | `id` | UUID (String 36) | Primary Key |
| | `booking_id` | UUID FK, Unique | **UniqueConstraint**: Guarantees at most 1 payment per booking |
| | `provider_payment_id` | String(128) | Unique external payment identifier |
| | `idempotency_key` | String(128) | Unique nullable client idempotency key |
| | `idempotency_request_hash` | String(64) | SHA-256 hash of payload; detects key reuse with mismatched body |
| **payment_events** | `id` | UUID (String 36) | Primary Key |
| | `event_id` | String(128) | **Unique constraint**: True idempotency ledger for webhooks |
| | `received_at` | DateTime(timezone=True) | Audit log timestamp of webhook arrival |

---

### Database-Enforced Guarantees

1. **Slot Double-Booking Prevention**:  
   Guaranteed by `UniqueConstraint("centre_id", "test_id", "appointment_at")`. Even if two concurrent requests pass application-level validation, PostgreSQL serializes the transaction and returns an `IntegrityError`, caught and converted into a `409 Conflict`.
2. **One Payment Per Booking**:  
   Enforced via `payments.booking_id UNIQUE`. Race conditions between direct payment and webhook ingestion cannot produce duplicate payment rows.
3. **Price Snapshotting**:  
   When a booking is created, the test's current price is copied into `bookings.amount`. If an admin later updates the test price, historical records and past invoices remain unchanged.
4. **Referential Integrity**:  
   Foreign keys use `ondelete="RESTRICT"` for core catalogues (centres cannot be deleted if active bookings exist) and `ondelete="CASCADE"` for payment audit events.

---

## State Machine & Execution Flow

### Booking Lifecycle State Diagram

```mermaid
stateDiagram-v2
    direction TB

    state "Booking: PENDING" as B_PENDING
    state "Booking: CONFIRMED" as B_CONFIRMED
    state "Booking: FAILED" as B_FAILED
    state "Booking: CANCELLED" as B_CANCELLED

    [*] --> B_PENDING : User books slot (appointment locked)

    B_PENDING --> B_CONFIRMED : Payment SUCCESS (Mock API or Webhook)
    B_PENDING --> B_FAILED : Payment FAILED (Mock API or Webhook)
    B_PENDING --> B_CANCELLED : User cancels booking

    B_CONFIRMED --> B_CANCELLED : User cancels confirmed booking

    B_CONFIRMED --> B_CONFIRMED : Duplicate SUCCESS Webhook (Idempotent 200 OK)
    B_FAILED --> B_FAILED : Duplicate FAILED Webhook (Idempotent 200 OK)

    note right of B_CONFIRMED : Terminal against contradictory webhooks.<br/>Cannot transition to FAILED (409 Conflict).
    note right of B_CANCELLED : Terminal state.<br/>Cannot be resurrected by any webhook (409 Conflict).
```

---

### Payment & Webhook Sequence Flow

```mermaid
sequenceDiagram
    autonumber
    actor Patient as 👤 Patient Client
    actor Gateway as 💳 Payment Gateway
    participant API as ⚡ FastAPI Backend
    participant PS as 📦 Payment Service
    participant DB as 🗄️ PostgreSQL

    rect rgb(245, 250, 255)
    Note over Patient,DB: Scenario 1: Client Direct Payment with Idempotency Key
    Patient->>API: POST /payments (booking_id, Idempotency-Key)
    API->>PS: process_mock_payment()
    PS->>DB: Check idempotency key & canonical request hash
    alt Idempotency Key Exists with Same Payload
        PS-->>API: Return existing Payment record
        API-->>Patient: 200 OK (Cached Payment Response)
    else New Payment Request
        PS->>DB: Lock Booking (SELECT FOR UPDATE)
        PS->>DB: Insert Payment (status=SUCCESS/FAILED)
        PS->>DB: Update Booking status (CONFIRMED/FAILED)
        PS->>DB: Commit Transaction
        API-->>Patient: 200 OK (Payment Processed)
    end
    end

    rect rgb(255, 250, 245)
    Note over Gateway,DB: Scenario 2: Asynchronous Webhook with Ledger Idempotency
    Gateway->>API: POST /payments/webhook (Header: X-Webhook-Signature)
    API->>API: Verify HMAC-SHA256(raw_body, secret)
    alt Invalid Signature
        API-->>Gateway: 401 Unauthorized
    else Signature Valid
        API->>PS: process_webhook(payload)
        PS->>DB: Query payment_events for event_id
        alt Duplicate event_id with identical payload
            PS-->>API: 200 OK (Idempotent replay - no duplicate processing)
        else Duplicate event_id with altered payload
            PS-->>API: 409 Conflict (Payload tamper detected)
        end
        opt Fresh event_id
            PS->>DB: Append to payment_events audit ledger
            PS->>DB: Lock and inspect Booking & Payment
            alt Booking is Terminal (CANCELLED or already CONFIRMED)
                PS-->>API: 409 Conflict (Terminal state guard)
            else State Transition Valid
                PS->>DB: Update Payment & Booking status
                PS-->>API: 200 OK (Webhook Processed)
            end
        end
        API-->>Gateway: 200 OK / 409 Conflict
    end
    end
```

---

## Important Assumptions You Made

1. **Role-Based Access Separation**:
   - The assignment requirements specify public user signup, so all self-registered users receive the `PATIENT` role.
   - Managing diagnostic centres and tests requires elevated privileges (`ADMIN`). An initial bootstrap admin is created on application startup via `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD` defined in the environment.
2. **Appointment Scheduling & Slot Granularity**:
   - Each booking is modeled for a specific test at a diagnostic centre at a designated appointment timestamp.
   - Timestamps must include an explicit timezone offset (e.g. `+05:30` or `Z`) and be in the future. The system converts and stores all timestamps in UTC.
3. **Single Test per Booking**:
   - In accordance with the assignment scope, a booking maps to exactly one test at a diagnostic centre. Multi-item cart checkout can build on top of this model by grouping bookings under an order aggregate.
4. **Single Payment Record per Booking**:
   - The simplified billing lifecycle models one primary payment attempt per booking. If a payment succeeds, the booking transitions to `CONFIRMED`. If it fails, the booking marks as `FAILED` and allows a retry or remains terminal.
5. **Deterministic Testing vs Production Simulation**:
   - The mock payment endpoint provides a `force_status` parameter (`SUCCESS` or `FAILED`) solely for reproducible, deterministic test runs and demos. When omitted, it simulates realistic provider randomness.
6. **Webhook Signature Security Model**:
   - Rather than relying solely on network firewalls, webhooks require an HMAC-SHA256 signature in the `X-Webhook-Signature` header calculated across the raw request body bytes.
7. **Database Session & Concurrency Isolation**:
   - Database operations use pessimistic locking (`with_for_update()`) during critical state transitions (such as payment processing and cancellation) alongside database uniqueness constraints for race-safe fallback.

---

## Edge Cases Handled

- **Authentication & Authorization**:
  - Expired, forged, or missing JWT tokens return `401 Unauthorized`.
  - Patients attempting to view/cancel another patient's booking return `403 Forbidden`.
  - Non-admin users attempting catalogue mutations return `403 Forbidden`.
  - Duplicate email registrations return `409 Conflict`.
- **Catalogue & Booking Validation**:
  - Invalid UUIDs or non-existent tests/centres return `404 Not Found`.
  - Past or timezone-naive appointment dates return `422 Unprocessable Entity`.
  - Duplicate appointment slots return `409 Conflict`.
  - Duplicate centre names at the same location return `409 Conflict`.
- **Payment & Webhook Resilience**:
  - Payments for already `CONFIRMED` or `CANCELLED` bookings return `409 Conflict`.
  - Reusing an `Idempotency-Key` with a different payload returns `409 Conflict`.
  - Webhooks with missing or mismatched HMAC-SHA256 signatures return `401 Unauthorized`.
  - Duplicate webhook delivery (`event_id`) is recognized as a replay and safely returns `200 OK` without duplicate processing.
  - Replayed `event_id` with a mutated payload returns `409 Conflict`.
  - Contradictory webhooks (e.g. `FAILED` arriving after a payment is already `CONFIRMED`) return `409 Conflict`.
  - Webhooks arriving for a booking that has already been `CANCELLED` are rejected and cannot resurrect the booking.
  - Webhooks referencing an amount different from the booked test price return `409 Conflict`.
- **System Robustness**:
  - `get_db()` dependency implements explicit rollback on exception to prevent uncommitted connection leaks to the pool.
  - Per-process in-memory rate limiting rejects abuse with `429 Too Many Requests` and `Retry-After`.

---

## What You Would Improve If You Had More Time

1. **Distributed Caching & Redis Integration**:
   - Replace the in-memory token bucket rate limiter with a Redis-backed sliding window limiter (`fastapi-limiter`) so multiple Uvicorn worker replicas share exact quota counters.
   - Store idempotency keys and cached centre listings in Redis with TTLs for sub-millisecond retrieval.
2. **Asynchronous Background Task Queue (Celery / ARQ / RabbitMQ)**:
   - Move non-critical webhook side effects (sending email confirmation, SMS appointment reminders, generating PDF invoices) into an asynchronous task queue.
3. **Calendar Capacity & Slot Inventory Engine**:
   - Transition from arbitrary timestamps to an explicit slot inventory system (e.g., 30-minute intervals, operating hours 08:00–20:00, holiday blackouts, and max concurrent appointment capacity per doctor/machine).
4. **Production Payment Gateway Integrations**:
   - Integrate official SDKs for Stripe and Razorpay, including automatic webhook event verification, refund lifecycles, and partial payment handling.
5. **Enhanced Authentication & Security**:
   - Implement refresh token rotation, token revocation blocklists, Multi-Factor Authentication (MFA), and email verification links.
6. **Observability, Tracing & Metrics**:
   - Export Prometheus metrics (`/metrics`) tracking request latency percentiles (p50, p95, p99), error rates, and active DB pool connections.
   - Integrate OpenTelemetry for distributed end-to-end request tracing into Jaeger or Grafana Tempo.
7. **CI/CD & Container Hardening**:
   - Run production Docker containers as a non-root unprivileged user (`USER 1000:1000`).
   - Add automated container vulnerability scanning (Trivy) and static application security testing (Bandit/Semgrep) to GitHub Actions.
