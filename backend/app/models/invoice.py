from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )

    # Identity in the CRM (populated once the draft is created)
    crm_invoice_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    invoice_number: Mapped[Optional[str]] = mapped_column(String(64), index=True)

    title: Mapped[str] = mapped_column(String(512), default="")
    invoice_type: Mapped[str] = mapped_column(String(32), default="Invoice")
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)

    grand_total: Mapped[float] = mapped_column(Float, default=0.0)
    subtotal: Mapped[float] = mapped_column(Float, default=0.0)
    tax_amount: Mapped[float] = mapped_column(Float, default=0.0)
    due_amount: Mapped[float] = mapped_column(Float, default=0.0)

    has_variance: Mapped[bool] = mapped_column(default=False)
    variance_amount: Mapped[float] = mapped_column(Float, default=0.0)

    # Full request payload sent to the CRM (line items, contact, franchise, ...)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    event = relationship("Event", back_populates="invoices")
