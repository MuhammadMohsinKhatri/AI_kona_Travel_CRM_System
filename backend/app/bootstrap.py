"""One-time startup: create tables (dev) and seed the first admin user."""
from __future__ import annotations

from app.config import settings
from app.db.base import Base, SessionLocal, engine
from app.models import User
from app.security import hash_password


def init_db() -> None:
    # In production use Alembic migrations; create_all keeps dev/SQLite frictionless.
    Base.metadata.create_all(bind=engine)
    seed_admin()


def seed_admin() -> None:
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == settings.first_admin_email).one_or_none()
        if existing is None:
            db.add(User(
                email=settings.first_admin_email,
                hashed_password=hash_password(settings.first_admin_password),
                full_name="Administrator",
                is_admin=True,
                is_active=True,
            ))
            db.commit()
    finally:
        db.close()
