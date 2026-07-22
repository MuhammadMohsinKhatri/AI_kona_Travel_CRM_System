from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class EventSummary(BaseModel):
    id: int
    crm_event_id: str
    event_code: Optional[str] = None
    event_name: str
    brand: str
    event_date: Optional[str] = None
    final_status: str
    event_type: str
    billing_model: str
    final_invoice_amount: float
    status: str
    status_reason: str = ""
    # Populated for status="error" — the actual failure text, so an errored
    # event is diagnosable from the list without opening its detail page.
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AlertOut(BaseModel):
    id: int
    severity: str
    issue: str
    action: str
    resolved: bool
    created_at: datetime
    # Which event this is about. Without it an alert is unactionable — "rate
    # per serving is missing" means nothing if you can't tell whose.
    # None for system-level alerts (e.g. an expired KonaOS session key).
    event_id: Optional[int] = None
    event_name: Optional[str] = None
    crm_event_id: Optional[str] = None
    event_date: Optional[str] = None
    brand: Optional[str] = None
    source: str = "financial"
    notified: bool = False
    notify_error: str = ""

    model_config = {"from_attributes": True}


class InvoiceOut(BaseModel):
    id: int
    event_id: int
    crm_invoice_id: Optional[str] = None
    invoice_number: Optional[str] = None
    title: str
    invoice_type: str
    status: str
    grand_total: float
    subtotal: float
    tax_amount: float
    due_amount: float
    has_variance: bool
    variance_amount: float
    payload: dict[str, Any]
    created_at: datetime
    # Proxied from the related event (Invoice has no date/name of its own) —
    # what the Invoices page filters and displays by.
    event_date: Optional[str] = None
    event_name: str = ""
    event_code: Optional[str] = None
    brand: str = ""

    model_config = {"from_attributes": True}


class EventDetail(EventSummary):
    raw: dict[str, Any]
    cleaned: dict[str, Any]
    classification: dict[str, Any]
    square: dict[str, Any]
    calculations: dict[str, Any]
    invoices: list[InvoiceOut] = []
    alerts: list[AlertOut] = []
