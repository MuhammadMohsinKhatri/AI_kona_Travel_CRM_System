from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class FinancialEntry(Base):
    """The financial ledger — Postgres replacement for the monthly Google Sheet.

    Stores ALL 46 sheet columns (one row per event, upserted on every pipeline
    run). The dashboard shows the key subset; the full set is available via CSV
    export. Column names/order mirror the sheet's headers.
    """

    __tablename__ = "financial_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), unique=True, index=True
    )
    run_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Provenance: "pipeline" (computed by a run) or "sheet" (imported from the
    # legacy Google Sheet). The sheet importer only ever creates/refreshes
    # source="sheet" rows and never overwrites a pipeline-owned row; once the
    # pipeline processes an event its row flips back to "pipeline".
    source: Mapped[str] = mapped_column(String(16), default="pipeline", index=True)
    month: Mapped[Optional[str]] = mapped_column(String(8), index=True)  # YYYY-MM

    # 1-4 Identity
    event_date: Mapped[Optional[str]] = mapped_column(String(32), index=True)  # DATE
    crm_event_id: Mapped[str] = mapped_column(String(64), index=True)          # EVENT ID
    event_name: Mapped[str] = mapped_column(String(512), default="")           # EVENT
    event_code: Mapped[Optional[str]] = mapped_column(String(64))
    brand: Mapped[str] = mapped_column(String(128), default="", index=True)
    final_status: Mapped[str] = mapped_column(String(64), default="")
    event_type: Mapped[str] = mapped_column(String(64), default="")            # EVENT TYPE
    billing_model: Mapped[str] = mapped_column(String(64), default="")

    # 5-10 Square (populated when Square tokens are live)
    square_gross_sales: Mapped[float] = mapped_column(Float, default=0.0)      # Square: Gross Sales
    square_discounts: Mapped[float] = mapped_column(Float, default=0.0)        # Square: Discounts
    square_net_card: Mapped[float] = mapped_column(Float, default=0.0)         # Square: Net Sales (Card)
    square_card_tax: Mapped[float] = mapped_column(Float, default=0.0)         # Square: Card Tax
    square_tips_card: Mapped[float] = mapped_column(Float, default=0.0)        # Square: Tips (Card)
    square_cc_fee: Mapped[float] = mapped_column(Float, default=0.0)           # Square: CC Fee (4%)
    square_orders: Mapped[int] = mapped_column(Integer, default=0)
    square_device: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # 11-13 Cash
    cash_collected: Mapped[float] = mapped_column(Float, default=0.0)          # Cash Collected
    cash_tax: Mapped[float] = mapped_column(Float, default=0.0)                # Cash Tax
    cash_pre_tax: Mapped[float] = mapped_column(Float, default=0.0)            # Cash Pre-Tax

    # ── Human/automation overrides ──────────────────────────────────────────
    # Cash is counted after the event, so the classifier's value (scraped from
    # whatever the driver wrote in the notes) is a guess at best and 0 at
    # worst. A value here WINS over the classifier and survives re-runs —
    # without it, the next nightly run would silently recompute cash from the
    # notes and wipe the real figure.
    #
    # JSON rather than a column per field so adding the next overridable field
    # (deposit, taxable, payment method…) doesn't need a migration. Keys are
    # model attribute names: {"cash_collected": 412.50}
    overrides: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Provenance, keyed the same way, so the UI can show WHERE a figure came
    # from: {"cash_collected": {"source": "api", "by": "cash-bot", "at": "…"}}
    # source is one of: "api" (another automation), "manual" (typed by a
    # person), "ai" (classifier — the default when there's no override).
    override_meta: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Min-guarantee events can't be invoiced until cash is known: the invoice
    # IS the gap between (card + cash) and the minimum. The nightly run defers
    # them and sets this; posting cash clears it and settles the invoice.
    awaiting_cash: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # Snapshotted so the shortfall can be settled later without re-classifying.
    minimum_required: Mapped[float] = mapped_column(Float, default=0.0)

    # 14-22 Billing
    check_invoice: Mapped[float] = mapped_column(Float, default=0.0)           # Check / Invoice
    deposit: Mapped[float] = mapped_column(Float, default=0.0)                 # Deposit / Prepay
    taxable: Mapped[bool] = mapped_column(Boolean, default=True)               # Taxable?
    event_sales_collected: Mapped[float] = mapped_column(Float, default=0.0)   # Event Sales - Collected
    sales_tax: Mapped[float] = mapped_column(Float, default=0.0)               # Sales Tax Amount
    sales_dollars: Mapped[float] = mapped_column(Float, default=0.0)           # Sales $
    giveback_amount: Mapped[float] = mapped_column(Float, default=0.0)         # Giveback Amount
    net_event_sales: Mapped[float] = mapped_column(Float, default=0.0)         # Net Event Sales
    location_fee: Mapped[float] = mapped_column(Float, default=0.0)            # Location Fee

    # 23 workflow
    paid: Mapped[bool] = mapped_column(Boolean, default=False)                 # PAID?

    # 24-28 Staff
    worker_1: Mapped[str] = mapped_column(String(255), default="")             # WORKER 1
    worker_1_hours: Mapped[float] = mapped_column(Float, default=0.0)          # Hours
    worker_2: Mapped[str] = mapped_column(String(255), default="")             # WORKER 2
    worker_2_hours: Mapped[float] = mapped_column(Float, default=0.0)          # Hours_1
    hours_paid: Mapped[bool] = mapped_column(Boolean, default=False)           # HOURS PAID?

    # 29-31
    note: Mapped[str] = mapped_column(String, default="")                      # Note (classifier reasoning)
    invoice_drafted: Mapped[bool] = mapped_column(Boolean, default=False)      # Invoice drafted?
    invoice_sent: Mapped[bool] = mapped_column(Boolean, default=False)         # Invoice Sent?

    # AI tracking (per event) — "rule-based" means the deterministic parser
    # handled it and no LLM call was made (0 tokens, $0).
    ai_model: Mapped[str] = mapped_column(String(64), default="")
    ai_prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    ai_completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    ai_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    # 32-46 Classifier / calculation
    total_event_hours: Mapped[float] = mapped_column(Float, default=0.0)       # TOTAL_EVENT_HOURS
    attendee_count: Mapped[int] = mapped_column(Integer, default=0)            # ATTENDEE_COUNT
    base_amount: Mapped[float] = mapped_column(Float, default=0.0)             # BASE_AMOUNT
    hourly_rate: Mapped[float] = mapped_column(Float, default=0.0)             # HOURLY_RATE
    rate_per_serving: Mapped[float] = mapped_column(Float, default=0.0)        # RATE_PER_SERVING
    host_covers_shortfall: Mapped[bool] = mapped_column(Boolean, default=False)  # HOST_COVERS_SHORTFALL
    units_served: Mapped[float] = mapped_column(Float, default=0.0)            # UNITS_SERVED_TOTAL
    units_included: Mapped[float] = mapped_column(Float, default=0.0)          # UNITS_INCLUDED_IN_BASE
    payment_method: Mapped[str] = mapped_column(String(32), default="")        # PAYMENT_METHOD
    tax_mode: Mapped[str] = mapped_column(String(16), default="")              # TAX_MODE
    subtotal: Mapped[float] = mapped_column(Float, default=0.0)               # SUBTOTAL
    actual_sales: Mapped[float] = mapped_column(Float, default=0.0)            # ACTUAL_SALES
    mg_shortfall: Mapped[float] = mapped_column(Float, default=0.0)            # MG_SHORTFALL
    total_tax_rate: Mapped[float] = mapped_column(Float, default=0.0)          # TOTAL_TAX_RATE
    total_tax: Mapped[float] = mapped_column(Float, default=0.0)               # TOTAL_TAX

    # Derived rollups kept for the dashboard summary
    cc_fee: Mapped[float] = mapped_column(Float, default=0.0)
    invoice_total: Mapped[float] = mapped_column(Float, default=0.0)
    balance_due: Mapped[float] = mapped_column(Float, default=0.0)
    has_variance: Mapped[bool] = mapped_column(Boolean, default=False)
    variance_amount: Mapped[float] = mapped_column(Float, default=0.0)
    square_sales: Mapped[float] = mapped_column(Float, default=0.0)  # convenience = net card

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    event = relationship("Event", back_populates="financial_entry")
