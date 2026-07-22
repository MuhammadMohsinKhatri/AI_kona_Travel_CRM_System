from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Nullable: not every alert is about an event. A dead KonaOS session key
    # is a system-level problem with no event to attach to, but it still
    # belongs on the same Needs Attention page as everything else — one place
    # to look is the whole point.
    event_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True, nullable=True
    )

    # CRITICAL | HIGH | MEDIUM | LOW
    severity: Mapped[str] = mapped_column(String(16), index=True)
    issue: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text, default="")

    # Where the alert came from, so the UI can explain what to do about it:
    #   financial — raised by the nightly run's alert rules
    #   cash      — a min-guarantee event still waiting on its cash
    #   session   — the KonaOS session key needs replacing
    source: Mapped[str] = mapped_column(String(16), default="financial", index=True)
    # Whether a Telegram push went out (and why not, if it didn't). Keeps the
    # "did anyone actually get told?" question answerable.
    notified: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_error: Mapped[str] = mapped_column(String(255), default="")

    resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    event = relationship("Event", back_populates="alerts")
