"""Normalize a raw CRM event into the flat cleaned structure.

Port of the n8n "clean event data" node. Converts epoch-ms timestamps to
America/New_York wall-clock, flattens staff/equipment/contacts and resolves the
final event status.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def to_edt_iso(ts: Any) -> str:
    """Epoch-ms → 'YYYY-MM-DDTHH:MM:SS.000' in New York local time."""
    if ts is None or ts == "":
        return ""
    try:
        ms = float(ts)
    except (TypeError, ValueError):
        return ""
    dt = datetime.fromtimestamp(ms / 1000.0, tz=NY)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000")


def _normalize_status(status: Optional[str]) -> str:
    if not status:
        return ""
    s = str(status)
    return s[0].upper() + s[1:].lower()


def clean_event(raw: dict[str, Any], brand_name: str = "") -> dict[str, Any]:
    e = raw or {}

    staff_names = ", ".join(
        f"{s.get('firstName','') or ''} {s.get('lastName','') or ''}".strip()
        for s in (e.get("eventStaffsDtoList") or [])
    )
    equipment_names = ", ".join(
        str(a.get("assetName", "")) for a in (e.get("eventAssetsDtoList") or [])
    )
    equipment_ids = ", ".join(
        str(a.get("assetId", "")) for a in (e.get("eventAssetsDtoList") or [])
    )

    manual_status = _normalize_status(e.get("manualStatus"))
    system_status = _normalize_status(e.get("eventStatus"))

    if system_status == "Cancelled":
        final_status = "Cancelled"
    elif manual_status:
        final_status = manual_status
    elif system_status:
        final_status = system_status
    else:
        final_status = "Unknown"

    location = ", ".join(
        p
        for p in [
            e.get("addressLine1"),
            e.get("addressLine2"),
            e.get("city"),
            e.get("state"),
            e.get("zipCode"),
        ]
        if p
    )

    return {
        "DATE": to_edt_iso(e.get("startDateTime")).split("T")[0],
        "EVENT_STARTED": to_edt_iso(e.get("startDateTime")),
        "EVENT_ENDED": to_edt_iso(e.get("endDateTime")),
        "EVENT_ID": e.get("id"),
        "EVENT_CODE": e.get("eventCode"),
        "EVENT_NAME": e.get("name") or "",
        "BUSINESS_NAME": e.get("name") or "",
        "BRAND": brand_name or e.get("brandName") or "",
        "EVENT_LOCATION": location,
        "LOCATION": location,
        "ADDRESS_LINE_1": e.get("addressLine1") or "",
        "ADDRESS_LINE_2": e.get("addressLine2") or "",
        "CITY": e.get("city") or "",
        "STATE": e.get("state") or "",
        "ZIP_CODE": e.get("zipCode") or "",
        "COUNTRY": e.get("country") or "",
        "LATITUDE": e.get("addressLatitude") or "",
        "LONGITUDE": e.get("addressLongitude") or "",
        "CONTACT_NAME": e.get("contactName") or "",
        "CONTACT_PHONE": f"{e.get('contactPhoneNumCountryCode','') or ''}{e.get('contactPhoneNumber','') or ''}",
        "CONTACT_EMAIL": e.get("contactEmail") or "",
        "SECONDARY_CONTACT_NAME": e.get("secondaryContactName") or "",
        "SECONDARY_CONTACT_PHONE": f"{e.get('secondaryContactPhoneNumCountryCode','') or ''}{e.get('secondaryContactPhoneNumber','') or ''}",
        "SECONDARY_CONTACT_EMAIL": e.get("secondaryContactEmail") or "",
        "STAFF_ASSIGNED": staff_names,
        "STAFF_COUNT": len(e.get("eventStaffsDtoList") or []),
        "EQUIPMENT": equipment_names,
        "EQUIPMENT_IDS": equipment_ids,
        "EVENT_STATUS_MANUAL": manual_status,
        "EVENT_STATUS_SYSTEM": system_status,
        "FINAL_EVENT_STATUS": final_status,
        "EVENT_NOTES_HTML": e.get("notes") or "",
        "ADMIN_NOTES": e.get("adminNotes") or "",
        "DRIVER_NOTES": e.get("driverNotes") or "",
        "LOCATION_NOTES": e.get("locationNotes") or "",
        "DELIVERY_MESSAGE": e.get("deliveryMessage") or "",
        "EVENT_SALES": e.get("eventSales") or 0,
        "NET_EVENT_SALES": e.get("netEventSales") or 0,
        "BALANCE": e.get("balance") or 0,
        "SALES_TAX": e.get("salesTax") or 0,
        "TIP_AMOUNT": e.get("tipAmount") or 0,
        "DELIVERY_FEE": e.get("deliveryFee") or 0,
        "EVENT_TYPE": e.get("eventType") or "",
        "PAYMENT_TERM": e.get("paymentTerm") or "",
        "CLIENT_INVOICE": e.get("clientInvoice") or False,
        "PRE_ORDER": e.get("preOrder") or False,
        "SOLD_OUT": e.get("soldOut") or False,
        "CREATED_AT": to_edt_iso(e.get("createdAt")),
        "UPDATED_AT": to_edt_iso(e.get("updatedAt")),
    }


def is_confirmed_or_completed(cleaned: dict[str, Any]) -> bool:
    """The n8n 'check if event is confirmed or completed' gate."""
    status = str(cleaned.get("FINAL_EVENT_STATUS") or "").strip().lower()
    return status in ("confirmed", "completed")
