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
    error: Optional[str] = None
    raw: dict[str, Any]
    cleaned: dict[str, Any]
    classification: dict[str, Any]
    square: dict[str, Any]
    calculations: dict[str, Any]
    invoices: list[InvoiceOut] = []
    alerts: list[AlertOut] = []
