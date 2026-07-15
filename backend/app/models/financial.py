from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FinancialEntry(Base):
    """The financial ledger — Postgres replacement for the monthly Google Sheet.

    One row per event, upserted on every pipeline run so it always reflects the
    latest calculation. Mirrors the sheet's columns plus the Square/KOS fields.
    """

    __tablename__ = "financial_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), unique=True, index=True
    )
    run_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Identity
    crm_event_id: Mapped[str] = mapped_column(String(64), index=True)
    event_code: Mapped[Optional[str]] = mapped_column(String(64))
    event_name: Mapped[str] = mapped_column(String(512), default="")
    brand: Mapped[str] = mapped_column(String(128), default="", index=True)
    event_date: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    month: Mapped[Optional[str]] = mapped_column(String(8), index=True)  # YYYY-MM
    final_status: Mapped[str] = mapped_column(String(64), default="")
    event_type: Mapped[str] = mapped_column(String(64), default="")
    billing_model: Mapped[str] = mapped_column(String(64), default="")

    # Financials (from the billing engine)
    units_served: Mapped[float] = mapped_column(Float, default=0.0)
    subtotal: Mapped[float] = mapped_column(Float, default=0.0)
    sales_tax: Mapped[float] = mapped_column(Float, default=0.0)
    tax_rate: Mapped[float] = mapped_column(Float, default=0.0)
    cc_fee: Mapped[float] = mapped_column(Float, default=0.0)
    invoice_total: Mapped[float] = mapped_column(Float, default=0.0)
    deposit: Mapped[float] = mapped_column(Float, default=0.0)
    balance_due: Mapped[float] = mapped_column(Float, default=0.0)
    payment_method: Mapped[str] = mapped_column(String(32), default="")
    cash_collected: Mapped[float] = mapped_column(Float, default=0.0)
    has_variance: Mapped[bool] = mapped_column(Boolean, default=False)
    variance_amount: Mapped[float] = mapped_column(Float, default=0.0)

    # Square reconciliation
    square_sales: Mapped[float] = mapped_column(Float, default=0.0)
    square_orders: Mapped[int] = mapped_column(Integer, default=0)
    square_device: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
