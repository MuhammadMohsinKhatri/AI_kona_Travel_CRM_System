"""Financial alert engine.

Faithful Python port of the n8n "Check Alerts" node (v4.6). Consumes the
classified event merged with its calculations block and returns a list of
``{severity, issue, action}`` alerts plus a formatted Telegram message.
"""
from __future__ import annotations

from typing import Any

_PRECISE_FIELD_MESSAGES: dict[str, dict[str, str]] = {
    "MISSING_RATE_PER_SERVING": {
        "severity": "HIGH",
        "issue": "Rate per serving is missing — invoice cannot be calculated",
        "action": "Add the rate per serving to event notes (e.g. '$5 per Kona')",
    },
    "MISSING_UNITS_SERVED_TOTAL": {
        "severity": "HIGH",
        "issue": "Serving count is missing — invoice cannot be calculated",
        "action": "Driver or admin must confirm actual serving count",
    },
    "MISSING_BASE_AMOUNT": {
        "severity": "HIGH",
        "issue": "Base or package amount is missing — invoice cannot be calculated",
        "action": "Add the base fee or package price to event notes (e.g. '$300 package' or '$99 base fee')",
    },
    "MISSING_UNITS_INCLUDED_IN_BASE": {
        "severity": "HIGH",
        "issue": "Included serving count is missing — cannot calculate overage charges",
        "action": "Add the number of servings included in the package to event notes (e.g. '50 servings included')",
    },
    "MISSING_OVERAGE_RATE": {
        "severity": "HIGH",
        "issue": "Overage occurred but no per-serving overage rate is stated — overage charge cannot be calculated",
        "action": "Add the overage rate to event notes (e.g. '$2.40 per extra serving') so the overage can be billed",
    },
    "MISSING_HOURLY_RATE": {
        "severity": "HIGH",
        "issue": "Hourly rate is missing — invoice cannot be calculated",
        "action": "Add the hourly rate to event notes (e.g. '$200 per hour')",
    },
    "MISSING_TOTAL_EVENT_HOURS": {
        "severity": "HIGH",
        "issue": "Event duration is missing — hourly invoice cannot be calculated",
        "action": "Confirm total event hours in notes or from scheduled start/end times",
    },
    "MISSING_GIVEBACK_PERCENTAGE": {
        "severity": "HIGH",
        "issue": "Giveback percentage is missing — giveback amount cannot be calculated",
        "action": "Add the giveback percentage to admin notes (e.g. 'Giveback Percentage: 20%')",
    },
    "MISSING_MINIMUM_AMOUNT_PER_HOUR": {
        "severity": "HIGH",
        "issue": "Minimum guarantee per hour is missing — billing floor cannot be set",
        "action": "Add the minimum per hour to event notes (e.g. '$250 minimum per hour')",
    },
    "MISSING_MINIMUM_FLAT_AMOUNT": {
        "severity": "HIGH",
        "issue": "Flat minimum guarantee amount is missing — billing floor cannot be set",
        "action": "Add the flat minimum to event notes (e.g. '$500 minimum guarantee')",
    },
    "MISSING_MINIMUM_AMOUNT": {
        "severity": "HIGH",
        "issue": "Minimum guarantee amount is missing — billing floor cannot be set",
        "action": "Add either a per-hour minimum or flat minimum to event notes",
    },
    "MISSING_HOST_SUBSIDY_PER_SERVING": {
        "severity": "HIGH",
        "issue": "Host subsidy per serving is missing — host contribution cannot be calculated",
        "action": "Add host subsidy amount to event notes (e.g. 'Host pays $3 per serving')",
    },
    "MISSING_GUEST_RATE_PER_SERVING": {
        "severity": "HIGH",
        "issue": "Guest rate per serving is missing — guest payment cannot be calculated",
        "action": "Add guest rate to event notes (e.g. 'Guests pay $2 per serving')",
    },
}

_SERVING_REQUIRED_MODELS = [
    "INVOICE_PER_SERVING",
    "INVOICE_BASE_FEE_PLUS_SERVINGS",
    "INVOICE_FIXED_PACKAGE",
    "INVOICE_HOURLY",
    "HYBRID_HOST_BASE_PLUS_GUEST_EXTRA",
    "HYBRID_HOST_SUBSIDY_PLUS_GUEST_PAYMENT",
    "HYBRID_SELLING_PLUS_MIN_GUARANTEE",
]

_SERVING_COUNT_MESSAGES = {
    "INVOICE_PER_SERVING": {
        "issue": "Serving count is 0 — invoice cannot be calculated (billed per serving)",
        "action": "Driver or admin must confirm actual serving count",
    },
    "INVOICE_FIXED_PACKAGE": {
        "issue": "Serving count is 0 — cannot check if extra servings were charged",
        "action": "Driver must confirm actual servings vs. included in package",
    },
    "INVOICE_BASE_FEE_PLUS_SERVINGS": {
        "issue": "Serving count is 0 — serving portion of invoice cannot be calculated",
        "action": "Driver must confirm actual serving count to complete invoice",
    },
    "INVOICE_HOURLY": {
        "issue": "Serving count is 0 — serving add-on cannot be calculated",
        "action": "Driver must confirm actual serving count if servings were charged separately",
    },
    "HYBRID_HOST_BASE_PLUS_GUEST_EXTRA": {
        "issue": "Serving count is 0 — cannot calculate guest extra serving charges",
        "action": "Driver must confirm total servings to check overage beyond included count",
    },
    "HYBRID_HOST_SUBSIDY_PLUS_GUEST_PAYMENT": {
        "issue": "Serving count is 0 — host and guest amounts cannot be calculated",
        "action": "Driver must confirm actual serving count to split host vs guest charges",
    },
    "HYBRID_SELLING_PLUS_MIN_GUARANTEE": {
        "issue": "Serving count is 0 — cannot calculate guest sales for shortfall reconciliation",
        "action": "Driver must confirm actual serving count for Square/cash reconciliation",
    },
}

_MISSING_PRICING_ALERTS = [
    "MISSING_RATE_PER_SERVING",
    "MISSING_BASE_AMOUNT",
    "MISSING_HOURLY_RATE",
    "MISSING_MINIMUM_AMOUNT_PER_HOUR",
    "MISSING_MINIMUM_FLAT_AMOUNT",
    "MISSING_MINIMUM_AMOUNT",
    "MISSING_BILLING_MODEL",
    "MISSING_OVERAGE_RATE",
]


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def check_alerts(data: dict[str, Any]) -> dict[str, Any]:
    alerts: list[dict[str, str]] = []
    calc = data.get("calculations") or {}

    def add(severity: str, issue: str, action: str) -> None:
        alerts.append({"severity": severity, "issue": issue, "action": action})

    event_name = data.get("EVENT_NAME") or calc.get("EVENT_NAME") or "Unknown"
    event_date = data.get("EVENT_DATE") or data.get("DATE") or ""
    event_type = str(data.get("EVENT_TYPE") or calc.get("EVENT_TYPE") or "").strip()
    billing_model = str(data.get("BILLING_MODEL") or calc.get("BILLING_MODEL") or "").strip()
    event_type_u = event_type.upper()
    billing_model_u = billing_model.upper()

    serving_number = _num(data.get("UNITS_SERVED_TOTAL"))
    invoice_total = _num(calc.get("FINAL_INVOICE_AMOUNT") or calc.get("CALCULATED_INVOICE_AMOUNT"))
    subtotal = _num(calc.get("SUBTOTAL"))
    cash_collected = _num(data.get("CASH_COLLECTED_AMOUNT"))
    mg_shortfall = _num(calc.get("MG_SHORTFALL"))

    ai_alerts = data.get("ALERT") if isinstance(data.get("ALERT"), list) else []

    is_selling = billing_model_u in ("SELLING_OPEN", "SELLING_WITH_GIVEBACK")
    is_mg = billing_model_u in (
        "MIN_GUARANTEE_HOURLY",
        "MIN_GUARANTEE_FLAT",
        "HYBRID_SELLING_PLUS_MIN_GUARANTEE",
    )

    # SECTION 2 — tax
    if "MISSING_TAX_EXEMPT" in ai_alerts and not is_selling:
        add("MEDIUM", "Tax exempt status not specified in notes",
            "Defaulted to TAXABLE=YES (6%) — confirm tax exempt status with client")

    # SECTION 2b — tax exempt unverified
    if "TAX_EXEMPT_UNVERIFIED" in ai_alerts:
        add("HIGH",
            "Tax exempt applied but not verified — no tax exempt language found in notes",
            "AI set TAXABLE=NO based on org type inference. Confirm tax exempt status with client and add explicit language to notes (e.g. 'school is tax exempt'). If not exempt, invoice must be recalculated with 6% tax.")

    # SECTION 3 — duration
    if "DURATION_CONFLICT_OVERRIDDEN" in ai_alerts:
        add("MEDIUM", "Event duration in notes differs from scheduled duration",
            "TOTAL_EVENT_HOURS was updated from notes — verify actual event time is correct")

    # SECTION 4 — billing model
    if "MISSING_BILLING_MODEL" in ai_alerts:
        add("HIGH",
            "Billing model cannot be determined — no pricing structure found in notes",
            "Add pricing details to event notes (who pays, rate, package amount, or minimum)")

    # SECTION 5 — precise field-level missing values
    for key, msg in _PRECISE_FIELD_MESSAGES.items():
        if key in ai_alerts:
            if is_selling and key in ("MISSING_RATE_PER_SERVING", "MISSING_UNITS_SERVED_TOTAL"):
                continue  # Square supplies actuals for selling events
            add(msg["severity"], msg["issue"], msg["action"])

    # SECTION 6 — serving count
    if "MISSING_SERVING_COUNT" in ai_alerts and not is_selling:
        add("HIGH", "Serving count missing from all notes",
            "Driver or admin must confirm actual serving count to complete event record")

    # SECTION 7 — payment
    if "PAYMENT_STATUS_UNCLEAR" in ai_alerts and not is_selling:
        payment_method = str(data.get("PAYMENT_METHOD") or "").upper().strip()
        cash_no_amount = payment_method == "CASH" and _num(data.get("CASH_COLLECTED_AMOUNT")) == 0
        if cash_no_amount:
            add("HIGH", "Cash payment confirmed but amount not recorded",
                "Driver confirmed payment in cash but did not write the dollar amount — contact driver to confirm exact cash collected")
        else:
            add("HIGH", "Payment status is unclear — paid but no amount recorded",
                "Review DRIVER_NOTES and ADMIN_NOTES — confirm exact amount collected and payment method")

    # SECTION 8 — calculation validations
    if not event_type or event_type_u == "UNDEFINED":
        add("HIGH", "Event type is missing or undefined",
            "Assign EVENT_TYPE — cannot route billing without it")

    if not billing_model or billing_model_u == "UNDEFINED":
        if "MISSING_BILLING_MODEL" not in ai_alerts:
            add("HIGH", "Billing model is UNDEFINED — invoice cannot be calculated",
                "Assign BILLING_MODEL subtype in event notes with pricing details")

    missing_invoice = invoice_total <= 0
    has_revenue = cash_collected > 0

    if missing_invoice and has_revenue and not is_selling:
        add("CRITICAL", "Revenue was collected but invoice total is $0 — data conflict",
            "Check pricing fields and billing model — calculation may have failed")

    if (
        missing_invoice
        and not is_selling
        and not is_mg
        and not any(a in _MISSING_PRICING_ALERTS for a in ai_alerts)
    ):
        add("HIGH", f"Invoice total is $0 for {billing_model or event_type} event",
            "Verify all required pricing fields are populated and recalculate")

    if (
        billing_model_u in _SERVING_REQUIRED_MODELS
        and serving_number <= 0
        and "MISSING_SERVING_COUNT" not in ai_alerts
        and "MISSING_UNITS_SERVED_TOTAL" not in ai_alerts
    ):
        sc = _SERVING_COUNT_MESSAGES.get(billing_model_u)
        if sc:
            add("HIGH", sc["issue"], sc["action"])

    # SECTION 9 — MG minimum + shortfall
    if is_mg:
        min_ph = _num(data.get("MINIMUM_AMOUNT_PER_HOUR"))
        min_flat = _num(data.get("MINIMUM_FLAT_AMOUNT"))
        if (
            min_ph == 0
            and min_flat == 0
            and "MISSING_MINIMUM_AMOUNT_PER_HOUR" not in ai_alerts
            and "MISSING_MINIMUM_FLAT_AMOUNT" not in ai_alerts
            and "MISSING_MINIMUM_AMOUNT" not in ai_alerts
        ):
            add("HIGH", "Minimum guarantee amount not found — billing floor cannot be set",
                "Add the guaranteed minimum to event notes (e.g. '$200/hr minimum' or '$500 flat minimum')")
        if mg_shortfall > 0:
            add("MEDIUM", f"Minimum guarantee shortfall of ${mg_shortfall:.2f}",
                "Host owes the shortfall amount — confirm collection before closing event")

    # SECTION 11 — unconfirmed discount / waiver
    if "UNCONFIRMED_DISCOUNT_OR_WAIVER" in ai_alerts:
        add("MEDIUM", "Unconfirmed discount or fee waiver detected in notes",
            "A discount or fee waiver was offered but not confirmed. Contact client to confirm — if accepted, update notes with confirmation language (e.g. '20% discount confirmed' or 'destination fee waived') and rerun to recalculate invoice.")

    telegram_message = _build_telegram_message(
        event_name, event_date, event_type, billing_model,
        invoice_total, subtotal, alerts,
    )

    return {
        "hasAlerts": len(alerts) > 0,
        "alertCount": len(alerts),
        "alerts": alerts,
        "telegramMessage": telegram_message,
    }


def _build_telegram_message(
    event_name, event_date, event_type, billing_model,
    invoice_total, subtotal, alerts,
) -> str:
    event_label = " — ".join(x for x in [event_name, event_date] if x)
    msg = (
        "🔔 *Financial Alert*\n\n"
        f"📋 *{event_label}*\n"
        f"🏷 Type: {event_type or 'Unknown'}  |  Model: {billing_model or 'Unknown'}\n"
        f"💰 Invoice: ${invoice_total:.2f}  |  Subtotal: ${subtotal:.2f}\n\n"
    )
    if not alerts:
        return msg + "✅ No issues detected — event looks clean."

    def group(icon, label, sev):
        items = [a for a in alerts if a["severity"] == sev]
        if not items:
            return ""
        body = "\n\n".join(f"• {a['issue']}\n  👉 {a['action']}" for a in items)
        return f"{icon} *{label}*\n{body}\n\n"

    msg += (
        group("🚨", "CRITICAL", "CRITICAL")
        + group("⚠️", "HIGH", "HIGH")
        + group("🔶", "MEDIUM", "MEDIUM")
        + group("ℹ️", "LOW", "LOW")
    )
    return msg
