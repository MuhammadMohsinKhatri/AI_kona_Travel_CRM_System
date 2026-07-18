from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base


class Event(Base):
    """A cleaned + classified event pulled from the Kona CRM.

    ``raw`` holds the original CRM payload, ``cleaned`` the normalized fields,
    ``classification`` the LLM output, ``square`` the reconciled sales and
    ``calculations`` the billing-engine result.
    """

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)

    # External identity
    crm_event_id: Mapped[str] = mapped_column(String(64), index=True)
    event_code: Mapped[Optional[str]] = mapped_column(String(64), index=True)

    # Queryable summary columns (denormalized from cleaned/classification)
    event_name: Mapped[str] = mapped_column(String(512), default="")
    brand: Mapped[str] = mapped_column(String(128), default="", index=True)
    event_date: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    final_status: Mapped[str] = mapped_column(String(64), default="", index=True)
    event_type: Mapped[str] = mapped_column(String(64), default="", index=True)
    billing_model: Mapped[str] = mapped_column(String(64), default="", index=True)

    final_invoice_amount: Mapped[float] = mapped_column(Float, default=0.0)

    # Pipeline processing state: pending | processing | processed | error | needs_review
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    # Why the pipeline gate made its call — e.g. "cancelled", "pending (no
    # equipment, no staff)", "confirmed". Shown under the status badge so a
    # skipped event explains itself.
    status_reason: Mapped[str] = mapped_column(String(255), default="")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Rich payloads
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cleaned: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    classification: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    square: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    calculations: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    run_id: Mapped[Optional[int]] = mapped_column(
        Integer, index=True, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    invoices = relationship(
        "Invoice", back_populates="event", cascade="all, delete-orphan"
    )
    alerts = relationship(
        "Alert", back_populates="event", cascade="all, delete-orphan"
    )
    # One ledger row per event. Declared so the ORM cascades the delete itself
    # rather than leaving it to the DB's FK: Postgres enforces ondelete=CASCADE
    # but SQLite only does with PRAGMA foreign_keys=ON, so relying on the FK
    # alone orphans the row in tests/local dev while passing in production.
    financial_entry = relationship(
        "FinancialEntry", back_populates="event",
        cascade="all, delete-orphan", uselist=False,
    )
