import os

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["JWT_SECRET_KEY"] = "test-secret"
os.environ["WEBHOOK_SECRET"] = "test-webhook"
os.environ["BOOTSTRAP_ADMIN_EMAIL"] = "admin@example.com"
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "Admin12345!"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Build the test engine before importing the app so the SessionLocal patch
# happens before the lifespan is registered.
engine = create_engine(
    "sqlite+pysqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

# Enable FK enforcement in SQLite so RESTRICT/CASCADE semantics match PostgreSQL.
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, _connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

# Patch the production SessionLocal BEFORE the app module is imported so the
# lifespan's ensure_bootstrap_admin call hits the test DB, not the real one.
import app.db.session as _db_session  # noqa: E402
_db_session.SessionLocal = TestingSession

from app.db.session import Base, get_db  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
import app.models  # noqa: F401, E402 — registers all SQLAlchemy models

Base.metadata.create_all(engine)


def override_db():
    db = TestingSession()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


fastapi_app.dependency_overrides[get_db] = override_db


@pytest.fixture
def client():
    with TestClient(fastapi_app) as c:
        yield c


@pytest.fixture(autouse=True)
def clean_db():
    from app.core.observability import rate_limiter
    rate_limiter._hits.clear()
    yield
    rate_limiter._hits.clear()
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    # Re-seed the bootstrap admin after each wipe so admin_auth() always works.
    from app.services.admin_service import ensure_bootstrap_admin
    from app.core.config import settings
    with TestingSession() as db:
        ensure_bootstrap_admin(db, settings.bootstrap_admin_email, settings.bootstrap_admin_password)

