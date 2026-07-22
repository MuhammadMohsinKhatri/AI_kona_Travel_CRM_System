from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base


class PipelineRun(Base):
    """One execution of the ingest → invoice pipeline."""

    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(primary_key=True)

    # running | completed | failed
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    trigger: Mapped[str] = mapped_column(String(32), default="manual")  # manual | scheduled
    # Optional YYYY-MM-DD filter: process only events on this date. Null = all events.
    target_date: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    # Optional scope filters, layered on target_date:
    #  • filter_event_types — only fully process events whose classified
    #    EVENT_TYPE is in this list (e.g. ["selling", "hybrid"]); others are
    #    skipped. Type is known only after classification, so filtering happens
    #    post-classify.
    #  • filter_event_ids — run these specific CRM event ids only (fetched
    #    directly, date-independent); when set, target_date is ignored.
    filter_event_types: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    filter_event_ids: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    events_fetched: Mapped[int] = mapped_column(Integer, default=0)
    events_processed: Mapped[int] = mapped_column(Integer, default=0)
    events_skipped: Mapped[int] = mapped_column(Integer, default=0)
    events_errored: Mapped[int] = mapped_column(Integer, default=0)
    invoices_created: Mapped[int] = mapped_column(Integer, default=0)
    alerts_raised: Mapped[int] = mapped_column(Integer, default=0)

    # AI usage (classifier) for this run
    ai_prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    ai_completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    ai_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    log: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # Live step tracker: [{key, label, status: pending|running|done|error, detail}]
    progress: Mapped[list[Any]] = mapped_column(JSON, default=list)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
