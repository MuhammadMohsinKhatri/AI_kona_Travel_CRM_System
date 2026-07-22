from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class PipelineRunOut(BaseModel):
    id: int
    status: str
    trigger: str
    target_date: Optional[str] = None
    filter_event_types: Optional[list[str]] = None
    filter_event_ids: Optional[list[str]] = None
    events_fetched: int
    events_processed: int
    events_skipped: int
    events_errored: int
    invoices_created: int
    alerts_raised: int
    ai_prompt_tokens: int = 0
    ai_completion_tokens: int = 0
    ai_cost_usd: float = 0.0
    error: Optional[str] = None
    log: list[Any] = []
    progress: list[Any] = []
    started_at: datetime
    finished_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class RunTriggerResponse(BaseModel):
    run_id: int
    mode: str  # "inline" | "queued"
    detail: str
