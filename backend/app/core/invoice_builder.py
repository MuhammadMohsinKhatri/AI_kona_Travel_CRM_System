"""Build the invoice-draft payload sent to the Kona CRM.

Port of the n8n "create invoice draft" node. Turns a classified + calculated
event into the CRM invoice request: line items per billing model, contact and
franchise fields, dates, deposit and totals.

Only INVOICE and HYBRID event types produce an invoice (selling/MG guest-paid
events settle via Square). ``build_invoice_payload`` returns ``None`` otherwise.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


def _num(v: Any) -> float:
    try:
        n = float(v)
        return 0.0 if n != n else n
    except (TypeError, ValueError):
        return 0.0


def _now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def _ny_date_to_utc_ms(date_str: str, now_ms: int) -> int:
    """'YYYY-MM-DD' → UTC ms at 04:00 (NY midnight-ish), matching the n8n node."""
    if not date_str:
        return now_ms
    try:
        y, m, d = (int(x) for x in date_str.split("-"))
        return int(datetime(y, m, d, 4, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    except (ValueError, TypeError):
        return now_ms


def _parse_location(location_str: str) -> dict[str, str]:
    if not location_str:
        return {"address": "", "city": "", "state": "", "zipCode": ""}
    parts = [s.strip() for s in location_str.split(",")]
    if len(parts) >= 4:
        return {"address": parts[0], "city": parts[1], "state": parts[2], "zipCode": parts[3]}
    if len(parts) == 3:
        return {"address": parts[0], "city": parts[1], "state": parts[2], "zipCode": ""}
    return {"address": location_str, "city": "", "state": "", "zipCode": ""}


def build_invoice_payload(
    event: dict[str, Any],
    cleaned: dict[str, Any],
    raw_event: dict[str, Any],
) -> Optional[dict[str, Any]]:
    e = event
    calc = e.get("calculations") or {}
    now = _now_ms()

    event_type_raw = str(e.get("EVENT_TYPE") or "").strip().upper()
    if event_type_raw not in ("INVOICE", "HYBRID"):
        return None
    invoice_type = "Invoice" if event_type_raw == "INVOICE" else "Hybrid"

    source_event = cleaned or {}
    raw = raw_event or {}

    parsed = _parse_location(source_event.get("LOCATION") or e.get("LOCATION") or "")

    event_date = _ny_date_to_utc_ms(
        source_event.get("DATE") or e.get("EVENT_DATE") or e.get("DATE") or "", now
    )
    due_date = now + 7 * 24 * 60 * 60 * 1000
    deposit_due_date = now + 2 * 24 * 60 * 60 * 1000
    invoice_resend_date = now + 3 * 24 * 60 * 60 * 1000

    final_invoice_amount = _num(calc.get("FINAL_INVOICE_AMOUNT"))
    calculated_invoice_amount = _num(calc.get("CALCULATED_INVOICE_AMOUNT"))
    subtotal_pre_tax = _num(calc.get("SUBTOTAL"))
    balance_due = _num(calc.get("BALANCE_DUE"))

    tax_rate = _num(calc.get("TAX_RATE"))
    sales_tax = _num(calc.get("SALES_TAX"))
    tax_amount = f"{sales_tax:.2f}"
    cc_fee = _num(calc.get("CC_FEE"))

    base_amount = _num(e.get("BASE_AMOUNT"))
    location_fee = _num(e.get("LOCATION_FEE"))
    deposit_amount = _num(e.get("DEPOSIT_AMOUNT"))
    rate_per_serving = _num(e.get("RATE_PER_SERVING"))

    overage_units = _num(calc.get("OVERAGE_UNITS"))
    overage_revenue = _num(calc.get("OVERAGE_REVENUE"))
    sales_amount = _num(calc.get("SALES_AMOUNT"))
    mg_shortfall = _num(calc.get("MG_SHORTFALL"))

    grand_total = final_invoice_amount

    line_items: list[dict[str, Any]] = []
    _id = 101
    billing_model = str(e.get("BILLING_MODEL") or "").upper().strip()

    def add_item(name, price, quantity, amount, taxable):
        nonlocal _id
        line_items.append({
            "id": _id, "name": name, "price": price,
            "quantity": quantity, "amount": amount, "taxable": taxable,
        })
        _id += 1

    if billing_model == "INVOICE_FIXED_PACKAGE":
        if base_amount > 0:
            add_item("Base Package", base_amount, 1, base_amount, True)
        if overage_units > 0 and overage_revenue > 0:
            add_item("Additional Servings (Overage)", rate_per_serving, overage_units, overage_revenue, True)

    elif billing_model == "INVOICE_PER_SERVING":
        units = _num(e.get("UNITS_SERVED_TOTAL"))
        if units > 0 and rate_per_serving > 0:
            add_item("Kona Ice Servings", rate_per_serving, units, round(units * rate_per_serving, 2), True)

    elif billing_model == "INVOICE_BASE_FEE_PLUS_SERVINGS":
        if base_amount > 0:
            add_item("Base Fee", base_amount, 1, base_amount, True)
        units = _num(e.get("UNITS_SERVED_TOTAL"))
        if units > 0 and rate_per_serving > 0:
            add_item("Kona Ice Servings", rate_per_serving, units, round(units * rate_per_serving, 2), True)

    elif billing_model == "INVOICE_HOURLY":
        total_hours = _num(e.get("TOTAL_EVENT_HOURS"))
        hourly_rate = _num(e.get("HOURLY_RATE"))
        if total_hours > 0 and hourly_rate > 0:
            add_item("Event Time", hourly_rate, total_hours, round(total_hours * hourly_rate, 2), True)
        units = _num(e.get("UNITS_SERVED_TOTAL"))
        if units > 0 and rate_per_serving > 0:
            add_item("Kona Ice Servings", rate_per_serving, units, round(units * rate_per_serving, 2), True)

    elif billing_model == "HYBRID_HOST_BASE_PLUS_GUEST_EXTRA":
        if base_amount > 0:
            add_item("Host Package", base_amount, 1, base_amount, True)
        if overage_units > 0 and overage_revenue > 0:
            add_item("Guest Extra Servings", rate_per_serving, overage_units, overage_revenue, True)

    elif billing_model in ("MIN_GUARANTEE_HOURLY", "MIN_GUARANTEE_FLAT"):
        if sales_amount > 0:
            add_item("Event Sales", rate_per_serving, _num(e.get("UNITS_SERVED_TOTAL")), sales_amount, True)
        if mg_shortfall > 0:
            add_item("Minimum Guarantee Shortfall", mg_shortfall, 1, mg_shortfall, True)

    else:
        if subtotal_pre_tax > 0:
            add_item("Event Services", subtotal_pre_tax, 1, subtotal_pre_tax, True)

    if location_fee > 0:
        add_item("Location / Destination Fee", location_fee, 1, location_fee, True)
    if cc_fee > 0:
        add_item("Credit Card Processing Fee", cc_fee, 1, cc_fee, False)

    # Contact cleanup
    raw_phone = str(source_event.get("CONTACT_PHONE") or e.get("CONTACT_PHONE") or raw.get("contactPhone") or "")
    contact_phone = raw_phone[2:] if raw_phone.startswith("+1") else raw_phone

    raw_franchise_phone = str(
        raw.get("franchisePhone") or raw.get("franchisePhoneNumber") or raw.get("franchise_phone") or ""
    )
    if raw_franchise_phone.startswith("+1"):
        raw_franchise_phone = raw_franchise_phone[2:]

    raw_county = str(raw.get("county") or raw.get("addressCounty") or e.get("county") or "").strip()
    county = raw_county if raw_county and raw_county.upper() != "USA" else ""

    franchise_email = (
        raw.get("franchiseEmail") or raw.get("franchise_email")
        or raw.get("franchiseContactEmail") or raw.get("ownerEmail") or ""
    )

    city = source_event.get("CITY") or e.get("CITY") or raw.get("city") or parsed["city"] or ""
    state = source_event.get("STATE") or e.get("STATE") or raw.get("state") or parsed["state"] or ""
    zip_code = source_event.get("ZIP_CODE") or e.get("ZIP_CODE") or raw.get("zipCode") or parsed["zipCode"] or ""
    address = source_event.get("LOCATION") or e.get("LOCATION") or parsed["address"] or ""

    return {
        "title": source_event.get("EVENT_NAME") or e.get("EVENT_NAME") or "Invoice",
        "invoiceNumber": raw.get("eventCode") or e.get("EVENT_CODE") or f"INV-{now}",
        "brandId": raw.get("brandId") or "",
        "clientId": raw.get("clientId") or "",
        "eventId": e.get("EVENT_ID") or "",
        "franchiseId": raw.get("franchiseId") or "",
        "eventDate": event_date,
        "invoiceDate": now,
        "dueDate": due_date,
        "businessName": source_event.get("EVENT_NAME") or e.get("EVENT_NAME") or "",
        "contactEmail": source_event.get("CONTACT_EMAIL") or e.get("CONTACT_EMAIL") or "",
        "contactPhoneNumCountryCode": "+1",
        "contactPhoneNumber": contact_phone,
        "contactName": source_event.get("CONTACT_NAME") or e.get("CONTACT_NAME") or "",
        "contactPersonId": "primary",
        "address": address,
        "city": city,
        "state": state,
        "county": county,
        "zipCode": zip_code,
        "ccEmailIds": franchise_email,
        "bccEmailIds": "",
        "franchiseName": raw.get("franchiseName") or "",
        "franchiseEmail": franchise_email,
        "franchisePhoneNumCountryCode": "+1",
        "franchisePhoneNumber": raw_franchise_phone,
        "franchiseAddress": raw.get("franchiseAddress") or "",
        "franchiseCity": raw.get("franchiseCity") or "",
        "franchiseState": raw.get("franchiseState") or "",
        "franchiseZipCode": raw.get("franchiseZipCode") or "",
        "clientInvoiceItems": line_items,
        "taxPercent": tax_rate,
        "taxAmount": tax_amount,
        "foodTotal": subtotal_pre_tax,
        "subTotal": subtotal_pre_tax,
        "taxableSubtotal": subtotal_pre_tax,
        "grandTotal": grand_total,
        "dueAmount": balance_due if balance_due > 0 else grand_total,
        "discountAmount": 0,
        "discountApplicableOnTax": True,
        "discountInAmount": True,
        "discountPercent": 0,
        "lateFeeAmount": 0,
        "lateFeeOverride": False,
        "_calculatedInvoiceAmount": calculated_invoice_amount,
        "_hasVariance": bool(calc.get("HAS_VARIANCE")),
        "_varianceAmount": _num(calc.get("VARIANCE_AMOUNT")),
        "_aiExtractedInvoice": _num(calc.get("AI_EXTRACTED_INVOICE_AMOUNT")),
        "invoiceType": invoice_type,
        "invoiceStatus": "draft",
        "saveAsDraft": True,
        "allowPartialPayment": True,
        "allowFullPayment": False,
        "depositRequired": deposit_amount > 0,
        "depositDueDate": deposit_due_date,
        "dipositInAmount": True,  # intentional API typo — do NOT rename
        "depositPercent": 0,
        "depositAmount": deposit_amount,
        "invoiceResendDate": invoice_resend_date,
        "attachmentFileIds": [],
    }
