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
        "EVENT_TYPE": "invoice", "BILLING_MODEL": "PACKAGE_FIXED",
        "BASE_AMOUNT": 1200, "UNITS_INCLUDED_IN_BASE": 600,
        "UNITS_SERVED_TOTAL": 536, "RATE_PER_SERVING": 0,
        "PRICE_IS_ALL_IN": "TRUE", "TAXABLE": "YES",
    })
    assert v == [], _issues(v)


def test_correct_base_plus_servings_passes():
    v = _check(BASE_PLUS_SERVINGS_NOTES, {
        "EVENT_TYPE": "invoice", "BILLING_MODEL": "PACKAGE_BASE_FEE_PLUS_SERVINGS",
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
        "EVENT_TYPE": "invoice", "BILLING_MODEL": "PACKAGE_FIXED",
        "BASE_AMOUNT": 1200, "UNITS_INCLUDED_IN_BASE": 600,
        "UNITS_SERVED_TOTAL": 536, "PRICE_IS_ALL_IN": "TRUE", "TAXABLE": "YES",
    })
    assert not any("2.00" in x["issue"] for x in v), _issues(v)


# ── every historical misbilling must be held ─────────────────────────────────

def test_all_in_quote_with_tax_added_is_held():
    # What actually shipped: $1,072 subtotal + 6% tax + 4% fee = $1,179.20.
    v = _check(FIXED_PACKAGE_NOTES, {
        "EVENT_TYPE": "invoice", "BILLING_MODEL": "PACKAGE_FIXED",
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
        "EVENT_TYPE": "invoice", "BILLING_MODEL": "PACKAGE_HOURLY",
        "HOURLY_RATE": 295, "TOTAL_EVENT_HOURS": 1,
        "UNITS_INCLUDED_IN_BASE": 60, "UNITS_SERVED_TOTAL": 60,
        "RATE_PER_SERVING": 4, "TAXABLE": "YES", "PAYMENT_METHOD": "CHECK",
    }
    assert billing.calculate_invoice(classification)["SUBTOTAL"] == 295.0
    v = _check(HOURLY_GUEST_EXTRA_NOTES, classification)
    assert any("guests pay for extra servings" in x["issue"] for x in v), _issues(v)


def test_taxable_disagreement_is_held():
    v = _check(BASE_PLUS_SERVINGS_NOTES, {
        "EVENT_TYPE": "invoice", "BILLING_MODEL": "PACKAGE_BASE_FEE_PLUS_SERVINGS",
        "BASE_AMOUNT": 99, "RATE_PER_SERVING": 4, "UNITS_SERVED_TOTAL": 23,
        "MINIMUM_FLAT_AMOUNT": 150, "TAXABLE": "NO",
    })
    assert any("TAXABLE Yes" in x["issue"] for x in v), _issues(v)


KIDDIE_ACADEMY_NOTES = {
    "EVENT_NOTES_HTML": (
        "EVENT TYPE Invoice ATTENDEES  SERVE & KEEP COUNT 12oz green cups TAXABLE "
        "Yes PAYMENT School will be paying - send invoice after"
    ),
    "ADMIN_NOTES": (
        "Teach our drivers to inform us if the client paid or we need to send them "
        'an invoice  $3 "kiddie" Kona\'s or our $4 "small" Kona\'s / The minimum '
        "for 1 hour will be $250"
    ),
    "DRIVER_NOTES": "22 green cups sold; send invoice.",
}


def test_a_host_purchase_classified_as_a_minimum_guarantee_is_held():
    """Kiddie Academy 2026-07-25 came back MIN_GUARANTEE_HOURLY. The subtotal was
    coincidentally right, so nothing looked wrong — but MG events are deferred
    until counted cash is posted, and on an invoice event no cash is ever posted.
    The invoice would never have been drafted and the school never billed.

    That silence is why this is CRITICAL: every other misclassification at least
    produces a wrong number somebody can see.
    """
    v = _check(KIDDIE_ACADEMY_NOTES, {
        "EVENT_TYPE": "minimum guarantee", "BILLING_MODEL": "MIN_GUARANTEE_HOURLY",
        "MINIMUM_AMOUNT_PER_HOUR": 250, "TOTAL_EVENT_HOURS": 1,
        "RATE_PER_SERVING": 4, "UNITS_SERVED_TOTAL": 22, "TAXABLE": "YES",
    })
    assert any("never mention guests paying" in x["issue"] for x in v), _issues(v)
    assert any(x["severity"] == "CRITICAL" for x in v), _issues(v)


def test_the_correct_invoice_reading_of_that_event_passes():
    """The same notes read correctly: per-serving with the minimum as a floor.
    Nothing fabricated, and the gate has nothing to say."""
    v = _check(KIDDIE_ACADEMY_NOTES, {
        "EVENT_TYPE": "invoice", "BILLING_MODEL": "PACKAGE_PER_SERVING",
        "RATE_PER_SERVING": 4, "UNITS_SERVED_TOTAL": 22,
        "MINIMUM_FLAT_AMOUNT": 250, "TAXABLE": "YES", "PAYMENT_METHOD": "CHECK",
    })
    assert not any("guests paying" in x["issue"] for x in v), _issues(v)


def test_a_genuine_minimum_guarantee_is_not_held():
    """Gallery Tower: "sell to guests with the $295 minimum". Guests DO pay, so the
    minimum has sales counting against it and MG is correct. The check must not
    fire here or it would block every real guarantee."""
    notes = {
        "ADMIN_NOTES": "Giveback percentage sell to guests with the $295 minimum",
        "DRIVER_NOTES": "Used terminal M",
        "EVENT_NOTES_HTML": "EVENT TYPE Selling ATTENDEES 80-100 people TAXABLE Yes",
    }
    v = _check(notes, {
        "EVENT_TYPE": "minimum guarantee", "BILLING_MODEL": "MIN_GUARANTEE_FLAT",
        "MINIMUM_FLAT_AMOUNT": 295, "TAXABLE": "YES",
    })
    assert not any("guests paying" in x["issue"] for x in v), _issues(v)


SELLING_NOTES = {
    "EVENT_NOTES_HTML": "EVENT TYPE Selling ATTENDEES 200 TAXABLE Yes",
    "ADMIN_NOTES": "Open selling event.",
    "DRIVER_NOTES": "",
}


def _selling(square, **overrides):
    classification = {
        "EVENT_TYPE": "selling", "BILLING_MODEL": "SELLING_OPEN",
        "TAXABLE": "YES", **overrides,
    }
    return check_invariants(
        SELLING_NOTES, classification,
        billing.calculate_invoice(classification), square,
    )


def test_a_selling_event_with_nothing_reconciled_is_flagged():
    """(IC) Pikesville Farmers Market, 2026-07-28, a five-hour selling event that
    reconciled $0.00. On a selling event the Square sales ARE the revenue, so this
    records none — and nothing in the system distinguished "sold nothing" from
    "Square not filled in for this event yet"."""
    v = _selling({"device_id": "415CS149B7001332", "order_count": 0,
                  "total_collected": 0.0, "breakdown": {"net_card": 0.0}})
    assert any("No sales at all were reconciled" in x["issue"] for x in v), _issues(v)
    assert any(x["severity"] == "CRITICAL" for x in v), _issues(v)


def test_an_unmapped_device_is_named_as_the_cause():
    """Same symptom, different cause — and the actionable one, so it gets its own
    message rather than the generic "no sales" wording."""
    v = _selling({"device_id": None, "order_count": 0, "total_collected": 0.0,
                  "breakdown": {}})
    assert any("no Square device could be matched" in x["issue"] for x in v), _issues(v)


def test_reconciled_card_sales_are_not_flagged():
    v = _selling({"device_id": "415CS149B7001332", "order_count": 12,
                  "total_collected": 340.0, "breakdown": {"net_card": 340.0}})
    assert not any("reconciled" in x["issue"] for x in v), _issues(v)


def test_a_cash_only_selling_event_is_not_flagged():
    """No Square at all is legitimate when the driver took cash — the money is
    accounted for, just not through a terminal."""
    v = _selling(
        {"device_id": None, "order_count": 0, "total_collected": 0.0, "breakdown": {}},
        CASH_COLLECTED_AMOUNT=210.0,
    )
    assert not any("reconciled" in x["issue"] for x in v), _issues(v)


def test_a_package_event_with_no_square_is_not_flagged():
    """Host-billed events are invoiced, not sold at the truck. Square is skipped
    for them by design, so zero is the expected value and must not raise."""
    classification = {
        "EVENT_TYPE": "package", "BILLING_MODEL": "PACKAGE_PER_SERVING",
        "RATE_PER_SERVING": 3, "UNITS_SERVED_TOTAL": 40, "TAXABLE": "YES",
    }
    v = check_invariants(
        {}, classification, billing.calculate_invoice(classification),
        {"device_id": None, "order_count": 0, "total_collected": 0.0,
         "breakdown": {}, "note": "skipped — invoice event"},
    )
    assert not any("reconciled" in x["issue"] for x in v), _issues(v)


def test_a_guarantee_event_with_no_sales_is_flagged():
    """Missing sales on an MG event are worse than a blank column: the shortfall
    the host is billed is the minimum MINUS those sales, so zero bills them the
    whole minimum."""
    classification = {
        "EVENT_TYPE": "minimum guarantee", "BILLING_MODEL": "MIN_GUARANTEE_FLAT",
        "MINIMUM_FLAT_AMOUNT": 295, "TAXABLE": "YES",
    }
    v = check_invariants(
        {"ADMIN_NOTES": "sell to guests with the $295 minimum"}, classification,
        billing.calculate_invoice(classification),
        {"device_id": "420CS149B7000809", "order_count": 0,
         "total_collected": 0.0, "breakdown": {"net_card": 0.0}},
    )
    assert any("No sales at all were reconciled" in x["issue"] for x in v), _issues(v)


def test_the_check_is_skipped_when_square_was_not_passed():
    """Callers that have no Square block (tests, the API, a recalculation) must
    not have this fire on absent data."""
    classification = {
        "EVENT_TYPE": "selling", "BILLING_MODEL": "SELLING_OPEN", "TAXABLE": "YES",
    }
    v = check_invariants({}, classification, billing.calculate_invoice(classification))
    assert not any("reconciled" in x["issue"] for x in v), _issues(v)


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
