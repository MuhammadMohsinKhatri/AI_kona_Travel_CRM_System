from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base


class CrmAuditEntry(Base):
    """Structured, permanent record of every write our system makes to
    KonaOS — event field updates and invoice create/delete/skip decisions.

    This is what a "what did your system change, and when" dispute gets
    checked against, instead of grepping a giant per-run text log. event_id
    and run_id are soft references (no FK/cascade) on purpose: this table
    must survive the referenced event being deleted-and-rebuilt or the run
    being pruned from history — the audit trail outlives both.
    """

    __tablename__ = "crm_audit_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    crm_event_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    event_name: Mapped[str] = mapped_column(String(512), default="")
    # Denormalized from the event at write time — filtering/display uses
    # THIS date (matching Financials/Invoices/Events convention: "date" means
    # the event's own date), not created_at. A run can process an event dated
    # days or weeks earlier than the moment it actually runs, so the two are
    # often different — created_at is kept separately as "when did this
    # write happen," a distinct and equally useful question.
    event_date: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    run_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)

    # event_updated | invoice_created | invoice_deleted | invoice_skipped
    action: Mapped[str] = mapped_column(String(32), default="", index=True)
    summary: Mapped[str] = mapped_column(String(512), default="")
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
