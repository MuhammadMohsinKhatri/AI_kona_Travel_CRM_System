"""In-memory mock implementations of every integration.

These let the entire pipeline run end-to-end without any external credentials.
Sample events are crafted to exercise several billing models, alert paths and
the Square reconciliation branch. Flip a provider to ``live`` in .env to swap
in the real HTTP client.
"""
from __future__ import annotations

import itertools
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from app.integrations.base import (
    Classifier,
    CRMClient,
    Notifier,
    SheetsClient,
    SquareClient,
)

_ID = itertools.count(1000)

# Simulated per-call latency so the live progress UI is observable in demos.
# Tests set MOCK_LATENCY_S=0 to run instantly.
_LATENCY = float(os.getenv("MOCK_LATENCY_S", "0.3"))


def _lag(factor: float = 1.0) -> None:
    if _LATENCY > 0:
        time.sleep(_LATENCY * factor)


def _ms(days_ago: int, hour: int) -> int:
    base = datetime.now(tz=timezone.utc) - timedelta(days=days_ago)
    dt = base.replace(hour=hour, minute=0, second=0, microsecond=0)
    return int(dt.timestamp() * 1000)


def _sample_events() -> list[dict[str, Any]]:
    """A spread of events covering the main billing models + alert cases."""
    return [
        {
            "id": "EVT-1001", "eventCode": "KI-1001", "name": "Lincoln Elementary Field Day",
            "brandName": "Kona Ice", "brandId": "brand-kona", "clientId": "cli-1", "franchiseId": "fr-1",
            "eventStatus": "completed", "manualStatus": "", "eventType": "invoice",
            "startDateTime": _ms(3, 14), "endDateTime": _ms(3, 16),
            "addressLine1": "1903 Lansdowne Road", "city": "Halethorpe", "state": "Maryland",
            "zipCode": "21227", "country": "USA",
            "contactName": "Sarah Miller", "contactPhoneNumCountryCode": "+1",
            "contactPhoneNumber": "4105551212", "contactEmail": "sarah@lincoln.edu",
            "eventStaffsDtoList": [{"firstName": "Dave", "lastName": "R"}],
            "eventAssetsDtoList": [{"assetName": "KEV1", "assetId": "A1"}],
            "adminNotes": "Setup fee $50 plus $3 per serving. Send invoice to school. School is tax exempt.",
            "driverNotes": "Served 120 cups total. Used KEV1.",
            "notes": "",
            "franchiseName": "Kona Baltimore", "franchiseEmail": "balt@kona.com",
        },
        {
            "id": "EVT-1002", "eventCode": "KI-1002", "name": "Riverside HOA Summer Bash",
            "brandName": "Kona Ice", "brandId": "brand-kona", "clientId": "cli-2", "franchiseId": "fr-1",
            "eventStatus": "confirmed", "manualStatus": "", "eventType": "invoice",
            "startDateTime": _ms(2, 12), "endDateTime": _ms(2, 15),
            "addressLine1": "88 River Rd", "city": "Columbia", "state": "Maryland",
            "zipCode": "21044", "country": "USA",
            "contactName": "Mark Jones", "contactPhoneNumber": "4435559090",
            "contactEmail": "mark@riverside.org",
            "eventStaffsDtoList": [{"firstName": "Lisa", "lastName": "T"}],
            "eventAssetsDtoList": [{"assetName": "KIOSK1", "assetId": "A2"}],
            "adminNotes": "If they purchase 40 Konas or more, charge $3 per Kona. Otherwise $125 minimum. Plus tax.",
            "driverNotes": "Served 55 cups. Ran KIOSK1.",
            "notes": "",
            "franchiseName": "Kona Baltimore", "franchiseEmail": "balt@kona.com",
        },
        {
            "id": "EVT-1003", "eventCode": "TT-1003", "name": "Downtown Coffee Popup",
            "brandName": "Travelin Tom", "brandId": "brand-tom", "clientId": "cli-3", "franchiseId": "fr-2",
            "eventStatus": "completed", "manualStatus": "", "eventType": "selling",
            "startDateTime": _ms(1, 8), "endDateTime": _ms(1, 11),
            "addressLine1": "200 Main St", "city": "Annapolis", "state": "Maryland",
            "zipCode": "21401", "country": "USA",
            "contactName": "Event Org", "contactPhoneNumber": "4105553434",
            "contactEmail": "org@downtown.com",
            "eventStaffsDtoList": [{"firstName": "Sam", "lastName": "P"}],
            "eventAssetsDtoList": [{"assetName": "KIOSK2 (SK)", "assetId": "A3"}],
            "adminNotes": "Open selling event. Guests pay individually via Square.",
            "driverNotes": "Used Kiosk 2. Square terminal all day.",
            "notes": "",
            "franchiseName": "Toms Annapolis", "franchiseEmail": "anna@toms.com",
        },
        {
            "id": "EVT-1004", "eventCode": "KI-1004", "name": "St. Anne Church Festival",
            "brandName": "Kona Ice", "brandId": "brand-kona", "clientId": "cli-4", "franchiseId": "fr-1",
            "eventStatus": "completed", "manualStatus": "", "eventType": "minimum guarantee",
            "startDateTime": _ms(4, 11), "endDateTime": _ms(4, 15),
            "addressLine1": "5 Chapel Ln", "city": "Towson", "state": "Maryland",
            "zipCode": "21204", "country": "USA",
            "contactName": "Father Tom", "contactPhoneNumber": "4105557878",
            "contactEmail": "office@stanne.org",
            "eventStaffsDtoList": [{"firstName": "Ken", "lastName": "M"}],
            "eventAssetsDtoList": [{"assetName": "KEV6", "assetId": "A4"}],
            "adminNotes": "Minimum guarantee $500 flat. Host covers shortfall. Guests pay via Square.",
            "driverNotes": "Served ~200 guests, used KEV6, Square terminal.",
            "notes": "",
            "franchiseName": "Kona Baltimore", "franchiseEmail": "balt@kona.com",
        },
        {
            "id": "EVT-1005", "eventCode": "KI-1005", "name": "Corporate Picnic (incomplete notes)",
            "brandName": "Kona Ice", "brandId": "brand-kona", "clientId": "cli-5", "franchiseId": "fr-1",
            "eventStatus": "completed", "manualStatus": "", "eventType": "invoice",
            "startDateTime": _ms(5, 10), "endDateTime": _ms(5, 13),
            "addressLine1": "700 Office Park", "city": "Hunt Valley", "state": "Maryland",
            "zipCode": "21030", "country": "USA",
            "contactName": "HR Dept", "contactPhoneNumber": "4105551000",
            "contactEmail": "hr@corp.com",
            "eventStaffsDtoList": [], "eventAssetsDtoList": [{"assetName": "MINI", "assetId": "A5"}],
            "adminNotes": "Host pays per serving. Send invoice.",
            "driverNotes": "Great event!",  # no serving count, no rate -> alerts
            "notes": "",
            "franchiseName": "Kona Baltimore", "franchiseEmail": "balt@kona.com",
        },
        {
            "id": "EVT-1006", "eventCode": "KI-1006", "name": "Cancelled Birthday",
            "brandName": "Kona Ice", "brandId": "brand-kona", "clientId": "cli-6", "franchiseId": "fr-1",
            "eventStatus": "cancelled", "manualStatus": "", "eventType": "invoice",
            "startDateTime": _ms(1, 17), "endDateTime": _ms(1, 19),
            "addressLine1": "12 Party Ave", "city": "Bel Air", "state": "Maryland",
            "zipCode": "21014", "country": "USA",
            "contactName": "Jane Doe", "contactPhoneNumber": "4105552222",
            "contactEmail": "jane@doe.com",
            "eventStaffsDtoList": [], "eventAssetsDtoList": [],
            "adminNotes": "Cancelled by client.", "driverNotes": "", "notes": "",
            "franchiseName": "Kona Baltimore", "franchiseEmail": "balt@kona.com",
        },
    ]


class MockCRMClient(CRMClient):
    def __init__(self) -> None:
        self._events = {e["id"]: e for e in _sample_events()}
        self._invoices: dict[str, dict[str, Any]] = {}

    def list_events(self, from_ms=None, to_ms=None) -> list[dict[str, Any]]:
        events = self._events.values()
        if from_ms is not None:
            events = [e for e in events if e.get("startDateTime", 0) >= from_ms]
        if to_ms is not None:
            events = [e for e in events if e.get("startDateTime", 0) < to_ms]
        return [{"id": e["id"], "eventCode": e["eventCode"], "name": e["name"]}
                for e in events]

    def get_event(self, event_id: str) -> dict[str, Any]:
        _lag(0.5)
        return self._events.get(event_id, {})

    def list_invoices(self) -> list[dict[str, Any]]:
        return list(self._invoices.values())

    def create_invoice(self, payload: dict[str, Any]) -> dict[str, Any]:
        _lag()
        inv_id = f"INV-{next(_ID)}"
        record = {"invoiceId": inv_id, "id": inv_id,
                  "eventId": payload.get("eventId"),
                  "invoiceNumber": payload.get("invoiceNumber"),
                  "status": "draft", **payload}
        self._invoices[inv_id] = record
        return record

    def delete_invoice(self, invoice_id: str) -> None:
        self._invoices.pop(invoice_id, None)

    def update_event(self, event_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if event_id in self._events:
            self._events[event_id].update({"_lastInvoiceUpdate": payload})
        return {"ok": True, "eventId": event_id}


class MockSquareClient(SquareClient):
    def search_orders(self, brand, device_id, date_iso):
        _lag()
        if not device_id:
            return {"brand": brand, "device_id": None, "order_count": 0,
                    "total_collected": 0.0, "payment_ids": [], "note": "no device mapped"}
        # Deterministic pseudo-sales derived from the device id.
        seed = sum(ord(c) for c in (device_id or "")) % 40
        total = round(150 + seed * 7.25, 2)
        return {
            "brand": brand, "device_id": device_id,
            "order_count": 8 + (seed % 5),
            "total_collected": total,
            "payment_ids": [f"pay_{device_id[-4:]}_{i}" for i in range(3)],
        }


class MockClassifier(Classifier):
    """Deterministic, rule-lite classifier good enough to drive the pipeline.

    Recognizes the sample events' note patterns. For unknown notes it falls back
    to a safe INVOICE_PER_SERVING guess and emits the appropriate MISSING_*
    alerts, mirroring how the real LLM would flag gaps.
    """

    def classify(self, cleaned: dict[str, Any]) -> dict[str, Any]:
        _lag(2.0)  # LLM calls are the slowest stage in real runs
        from app.schemas.classification import Classification

        admin = (cleaned.get("ADMIN_NOTES") or "").lower()
        driver = (cleaned.get("DRIVER_NOTES") or "").lower()
        allnotes = f"{admin} {driver} {(cleaned.get('EVENT_NOTES_HTML') or '').lower()}"

        c = Classification(
            EVENT_ID=str(cleaned.get("EVENT_ID") or ""),
            EVENT_NAME=cleaned.get("EVENT_NAME") or "",
            EVENT_DATE=cleaned.get("DATE") or "",
            ASSIGNED_EQUIPMENT=cleaned.get("EQUIPMENT") or "",
        )
        alerts: list[str] = []

        # Serving count
        import re
        m = re.search(r"served\s+~?(\d+)", driver) or re.search(r"(\d+)\s+cups", driver)
        if m:
            c.UNITS_SERVED_TOTAL = float(m.group(1))
            c.SERVING_COUNT_SOURCE = "Driver Notes"

        # Square / equipment
        if any(k in allnotes for k in ("square", "kiosk", "terminal", "kev", "k2", "k1")):
            c.SQUARE_USED = "TRUE"
            c.SQUARE_DEVICE_CONFIDENCE = "HIGH"
        dm = re.search(r"(kiosk ?\d|kev\d|mini)", driver)
        if dm:
            token = dm.group(1).upper().replace(" ", "")
            c.DRIVER_REPORTED_EQUIPMENT = {
                "KIOSK2": "KIOSK2 (SK)", "KIOSK1": "KIOSK1", "KEV6": "KEV6 (SK)",
                "KEV1": "KEV1 (SM)", "KEV2": "KEV2 (SM)", "KEV7": "KEV7", "MINI": "MINI",
            }.get(token, token)

        # Tax
        if "tax exempt" in allnotes:
            c.TAXABLE, c.TAX_RATE_sales = "NO", 0.0
        else:
            c.TAXABLE, c.TAX_RATE_sales = "YES", 0.06
        c.PROCESSING_FEE_RATE = 0.04

        # Billing model resolution
        if "minimum guarantee" in admin or "min guarantee" in admin:
            fm = re.search(r"\$(\d+)\s*flat|minimum guarantee \$(\d+)|\$(\d+)\s*minimum", admin)
            flat = next((g for g in (fm.groups() if fm else []) if g), None)
            c.EVENT_TYPE = "minimum guarantee"
            if "square" in allnotes:
                c.BILLING_MODEL = "HYBRID_SELLING_PLUS_MIN_GUARANTEE"
            else:
                c.BILLING_MODEL = "MIN_GUARANTEE_FLAT"
            c.MINIMUM_FLAT_AMOUNT = float(flat) if flat else 0.0
            c.PAYMENT_METHOD = "CHECK"
            if not c.MINIMUM_FLAT_AMOUNT and c.MINIMUM_AMOUNT_PER_HOUR == 0:
                alerts.append("MISSING_MINIMUM_AMOUNT")
        elif "open selling" in admin or "selling event" in admin or "guests pay" in admin:
            c.EVENT_TYPE = "selling"
            c.BILLING_MODEL = "SELLING_WITH_GIVEBACK" if "giveback" in allnotes else "SELLING_OPEN"
            c.PAYMENT_METHOD = "CREDIT_CARD"
            c.PAID_STATUS = True
        elif "or more" in admin and "minimum" in admin:
            # "charge $X per Kona if N or more, otherwise $Y minimum" => fixed package
            c.EVENT_TYPE = "invoice"
            c.BILLING_MODEL = "INVOICE_FIXED_PACKAGE"
            base = re.search(r"\$(\d+)\s*minimum", admin)
            incl = re.search(r"(\d+)\s+konas?\s+or more", admin)
            rate = re.search(r"\$(\d+(?:\.\d+)?)\s*per", admin)
            c.BASE_AMOUNT = float(base.group(1)) if base else 0.0
            c.UNITS_INCLUDED_IN_BASE = float(incl.group(1)) if incl else 0.0
            if c.UNITS_SERVED_TOTAL > c.UNITS_INCLUDED_IN_BASE and rate:
                c.RATE_PER_SERVING = float(rate.group(1))
            c.PAYMENT_METHOD = "CHECK"
        elif "setup fee" in admin or "base fee" in admin:
            c.EVENT_TYPE = "invoice"
            c.BILLING_MODEL = "INVOICE_BASE_FEE_PLUS_SERVINGS"
            base = re.search(r"(?:setup|base) fee \$(\d+)", admin)
            rate = re.search(r"\$(\d+(?:\.\d+)?)\s*per serving", admin)
            c.BASE_AMOUNT = float(base.group(1)) if base else 0.0
            c.RATE_PER_SERVING = float(rate.group(1)) if rate else 0.0
            c.PAYMENT_METHOD = "CHECK"
        elif "per serving" in admin or "per kona" in admin:
            c.EVENT_TYPE = "invoice"
            c.BILLING_MODEL = "INVOICE_PER_SERVING"
            rate = re.search(r"\$(\d+(?:\.\d+)?)\s*per", admin)
            c.RATE_PER_SERVING = float(rate.group(1)) if rate else 0.0
            c.PAYMENT_METHOD = "CHECK"
            if c.RATE_PER_SERVING == 0:
                alerts.append("MISSING_RATE_PER_SERVING")
            if c.UNITS_SERVED_TOTAL == 0:
                alerts.append("MISSING_UNITS_SERVED_TOTAL")
        else:
            c.EVENT_TYPE = "undefined"
            c.BILLING_MODEL = "UNDEFINED"
            alerts.append("MISSING_BILLING_MODEL")

        c.NOTE = "Mock classifier heuristic extraction"
        out = c.model_dump()
        out["ALERT"] = alerts
        return out


class MockSheetsClient(SheetsClient):
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def append_row(self, brand: str, row: dict[str, Any]) -> None:
        _lag(0.5)
        self.rows.append({"brand": brand, **row})


class MockNotifier(Notifier):
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, message: str) -> None:
        self.messages.append(message)
