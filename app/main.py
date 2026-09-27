from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.routes import router
from app.core.config import settings
from app.core.middleware import rate_limit_middleware, request_context_middleware
from app.core.observability import configure_logging
from app.db.session import SessionLocal
from app import models as _models  # noqa: F401 - registers all SQLAlchemy models
from app.services.admin_service import ensure_bootstrap_admin


@asynccontextmanager
async def lifespan(_: FastAPI):
    with SessionLocal() as db:
        ensure_bootstrap_admin(db, settings.bootstrap_admin_email, settings.bootstrap_admin_password)
    yield


configure_logging()

app = FastAPI(
    title="Diagnostic Booking API",
    version="2.0.0",
    description="Diagnostic centre booking service with simulated payments and idempotent webhook processing.",
    lifespan=lifespan,
)
app.middleware("http")(rate_limit_middleware)
app.middleware("http")(request_context_middleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Webhook-Signature"],
)
app.include_router(router, prefix="/api/v1")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def readiness():
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database is unavailable") from exc
