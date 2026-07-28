"""Pre-invoice consistency gate.

Two halves, and the second matters as much as the first: every historical
misbilling must be caught, AND every corrected version must pass silently. A
gate that holds correct events trains people to click past it.
"""
from app.core import billing
from app.core.invariants import check_invariants

# ── real note text from the events these rules come from ─────────────────────

FIXED_PACKAGE_NOTES = {
    "EVENT_NOTES_HTML": (
        "EVENT TYPE Invoice ATTENDEES 600 SERVE / KEEP COUNT 600 9oz yellow cups "
        "INCLUDE  ADD'L INSTRUCTION PARKING call me when you arrive "
        "PAYMENT School will be paying - send invoice after"
    ),
    "ADMIN_NOTES": (
        "(no extra taxes or fees). This price includes up to 600 9oz Kona Ice cups "
        "and Star-K certification. Our cost is $2 per cup, so the cost for 600 "
        "Kona's would come to $1,200"
    ),
    "DRIVER_NOTES": "Served 536 Konas. Send invoice.",
}

BASE_PLUS_SERVINGS_NOTES = {
    "EVENT_NOTES_HTML": (
        "EVENT TYPE Invoice ATTENDEES 16-30 SERVE & KEEP COUNT $4 12oz green cups "
        "ADD'L INSTRUCTION INCLUDE  PARKING There is a parking lot TAXABLE Yes "
        "PAYMENT $99 plus the cost of the amount of Kona's we serve (total minimum "
        "of $150 plus tax) / May pay by credit card or ask to invoice w/ tax. "
        "If paying w CC, add 4% CC fee (Multiply total by 1.04)"
    ),
    "ADMIN_NOTES": "Teach our drivers to inform us if the client paid or we need to send them an invoice",
    "DRIVER_NOTES": "23 blue cups sold; paid in full terminal 6",
}

HOURLY_GUEST_EXTRA_NOTES = {
    "EVENT_NOTES_HTML": (
        "EVENT TYPE Invoice ATTENDEES 60 SERVE & KEEP COUNT 60 12oz green Kona's  "
        "ADD'L INSTRUCTION I will provide each athlete with a ticket to give you to "
        "claim their one free Kona. Then, they will have to pay for the second one "
        "INCLUDE  PARKING will be right outside our building TAXABLE Yes "
        "PAYMENT send invoice after"
    ),
    "ADMIN_NOTES": (
        "$295 per hour, (plus tax), with each hour including up to 60 12oz green "
        "Kona's / Each additional Kona is $4"
    ),
    "DRIVER_NOTES": "60 green cups sold; send invoice.",
}


def _check(notes, classification):
    return check_invariants(notes, classification, billing.calculate_invoice(classification))


def _issues(violations):
    return " | ".join(v["issue"] for v in violations)


# ── the corrected classifications must pass clean ────────────────────────────

def test_correct_fixed_package_all_in_passes():
    v = _check(FIXED_PACKAGE_NOTES, {
        "EVENT_TYPE": "invoice", "BILLING_MODEL": "INVOICE_FIXED_PACKAGE",
        "BASE_AMOUNT": 1200, "UNITS_INCLUDED_IN_BASE": 600,
        "UNITS_SERVED_TOTAL": 536, "RATE_PER_SERVING": 0,
        "PRICE_IS_ALL_IN": "TRUE", "TAXABLE": "YES",
    })
    assert v == [], _issues(v)


def test_correct_base_plus_servings_passes():
    v = _check(BASE_PLUS_SERVINGS_NOTES, {
        "EVENT_TYPE": "invoice", "BILLING_MODEL": "INVOICE_BASE_FEE_PLUS_SERVINGS",
        "BASE_AMOUNT": 99, "RATE_PER_SERVING": 4, "UNITS_SERVED_TOTAL": 23,
        "MINIMUM_FLAT_AMOUNT": 150, "TAXABLE": "YES",
        "PAYMENT_METHOD": "CREDIT_CARD",
    })
    assert v == [], _issues(v)


def test_a_stated_per_unit_price_is_accounted_for_by_multiplication():
    """"our cost is $2 per cup" never appears as a field — it is legitimate only
    as 2 x 600 = the $1,200 base. Without the product check this would hold every
    package event that shows its per-cup arithmetic."""
    v = _check(FIXED_PACKAGE_NOTES, {
        "EVENT_TYPE": "invoice", "BILLING_MODEL": "INVOICE_FIXED_PACKAGE",
        "BASE_AMOUNT": 1200, "UNITS_INCLUDED_IN_BASE": 600,
        "UNITS_SERVED_TOTAL": 536, "PRICE_IS_ALL_IN": "TRUE", "TAXABLE": "YES",
    })
    assert not any("2.00" in x["issue"] for x in v), _issues(v)


# ── every historical misbilling must be held ─────────────────────────────────

def test_all_in_quote_with_tax_added_is_held():
    # What actually shipped: $1,072 subtotal + 6% tax + 4% fee = $1,179.20.
    v = _check(FIXED_PACKAGE_NOTES, {
        "EVENT_TYPE": "invoice", "BILLING_MODEL": "INVOICE_FIXED_PACKAGE",
        "BASE_AMOUNT": 1072, "UNITS_INCLUDED_IN_BASE": 600,
        "UNITS_SERVED_TOTAL": 536, "RATE_PER_SERVING": 0, "TAXABLE": "YES",
    })
    assert any("rule out extra tax" in x["issue"] for x in v), _issues(v)
    # The $1,200 the admin stated does no work in a $1,072 invoice.
    assert any("1,200.00" in x["issue"] for x in v), _issues(v)


def test_form_event_type_mismatch_is_held():
    # What shipped: classified hybrid, billed the bare $150 minimum.
    v = _check(BASE_PLUS_SERVINGS_NOTES, {
        "EVENT_TYPE": "hybrid", "BILLING_MODEL": "HYBRID_SELLING_PLUS_MIN_GUARANTEE",
        "MINIMUM_FLAT_AMOUNT": 150, "RATE_PER_SERVING": 4,
        "UNITS_SERVED_TOTAL": 23, "BASE_AMOUNT": 0, "TAXABLE": "YES",
    })
    assert any("EVENT TYPE 'invoice'" in x["issue"] for x in v), _issues(v)


def test_dropped_base_fee_is_held():
    """The generalised catch: the $99 is stated and the chosen model ignores it."""
    v = _check(BASE_PLUS_SERVINGS_NOTES, {
        "EVENT_TYPE": "invoice", "BILLING_MODEL": "HYBRID_SELLING_PLUS_MIN_GUARANTEE",
        "MINIMUM_FLAT_AMOUNT": 150, "RATE_PER_SERVING": 4,
        "UNITS_SERVED_TOTAL": 23, "BASE_AMOUNT": 0, "TAXABLE": "YES",
    })
    assert any("$99.00" in x["issue"] for x in v), _issues(v)


def test_guest_paid_extras_billed_to_host_is_held():
    """Notes: athletes "have to pay for the second one". Billing the host a
    per-serving rate on top would collect that money twice. Subtotal is right
    today only because served == included, so nothing but this check catches it."""
    classification = {
        "EVENT_TYPE": "invoice", "BILLING_MODEL": "INVOICE_HOURLY",
        "HOURLY_RATE": 295, "TOTAL_EVENT_HOURS": 1,
        "UNITS_INCLUDED_IN_BASE": 60, "UNITS_SERVED_TOTAL": 60,
        "RATE_PER_SERVING": 4, "TAXABLE": "YES", "PAYMENT_METHOD": "CHECK",
    }
    assert billing.calculate_invoice(classification)["SUBTOTAL"] == 295.0
    v = _check(HOURLY_GUEST_EXTRA_NOTES, classification)
    assert any("guests pay for extra servings" in x["issue"] for x in v), _issues(v)


def test_taxable_disagreement_is_held():
    v = _check(BASE_PLUS_SERVINGS_NOTES, {
        "EVENT_TYPE": "invoice", "BILLING_MODEL": "INVOICE_BASE_FEE_PLUS_SERVINGS",
        "BASE_AMOUNT": 99, "RATE_PER_SERVING": 4, "UNITS_SERVED_TOTAL": 23,
        "MINIMUM_FLAT_AMOUNT": 150, "TAXABLE": "NO",
    })
    assert any("TAXABLE Yes" in x["issue"] for x in v), _issues(v)


def test_gate_kill_switch_is_wired_and_defaults_on():
    """The gate sits in front of billing, so a bad rule could stall invoicing.
    `invoice_gate_enabled=false` must fall back to alert-only without a rebuild."""
    from app.config import settings
    from app.core.pipeline import _settings_gate_enabled

    assert settings.invoice_gate_enabled is True
    assert _settings_gate_enabled() is True
    original = settings.invoice_gate_enabled
    try:
        settings.invoice_gate_enabled = False
        assert _settings_gate_enabled() is False
    finally:
        settings.invoice_gate_enabled = original


def test_violations_carry_the_alert_shape():
    """They ride the existing alert machinery, so they must look like alerts."""
    v = _check(BASE_PLUS_SERVINGS_NOTES, {
        "EVENT_TYPE": "hybrid", "BILLING_MODEL": "HYBRID_SELLING_PLUS_MIN_GUARANTEE",
        "MINIMUM_FLAT_AMOUNT": 150, "UNITS_SERVED_TOTAL": 23, "TAXABLE": "YES",
    })
    assert v
    for item in v:
        assert set(item) == {"severity", "issue", "action"}
        assert item["severity"] in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
