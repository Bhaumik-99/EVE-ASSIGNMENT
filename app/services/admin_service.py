from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models import User, UserRole


def ensure_bootstrap_admin(db: Session, email: str | None, password: str | None) -> None:
    if not email or not password:
        return
    normalized = email.lower().strip()
    user = db.scalar(select(User).where(User.email == normalized))
    if user:
        return
    db.add(
        User(
            email=normalized,
            password_hash=hash_password(password),
            full_name="System Administrator",
            role=UserRole.ADMIN,
        )
    )
    db.commit()
