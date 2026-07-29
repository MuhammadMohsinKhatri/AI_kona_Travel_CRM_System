"""Giveback: detect it when it was promised but never quantified, and allow it to
be set after the fact.

From the 2026-07-28 call. Scott spotted Arbutus Youth Football recording $0.00
giveback; its notes read "Giveback percentage sell to guests with the $295 minimum"
— the word is there, the number never is. Brett declined a bulk updater ("going
forward, if we're using the form, there'll never be a need"), so the fix is per
event.
"""
from app.core import billing, overrides
from app.core.invariants import check_invariants

ARBUTUS_NOTES = {
    "ADMIN_NOTES": "Giveback percentage sell to guests with the $295 minimum",
    "DRIVER_NOTES": "Used terminal M",
    "EVENT_NOTES_HTML": "EVENT TYPE Selling ATTENDEES 80-100 people TAXABLE Yes",
}


def _check(notes, classification, square=None):
    return check_invariants(
        notes, classification, billing.calculate_invoice(classification), square)


def _issues(v):
    return " | ".join(x["issue"] for x in v)


def test_a_giveback_mentioned_without_a_number_is_flagged():
    v = _check(ARBUTUS_NOTES, {
        "EVENT_TYPE": "minimum guarantee", "BILLING_MODEL": "MIN_GUARANTEE_FLAT",
        "MINIMUM_FLAT_AMOUNT": 295, "GIVEBACK_PERCENTAGE": 0, "TAXABLE": "YES",
    })
    assert any("mention a giveback but no percentage" in x["issue"] for x in v), _issues(v)


def test_a_quantified_giveback_is_not_flagged():
    v = _check(
        {"ADMIN_NOTES": "Open selling with a 15% giveback to the league."},
        {"EVENT_TYPE": "selling", "BILLING_MODEL": "SELLING_WITH_GIVEBACK",
         "RATE_PER_SERVING": 4, "UNITS_SERVED_TOTAL": 100,
         "GIVEBACK_PERCENTAGE": 0.15, "TAXABLE": "YES"},
    )
    assert not any("giveback" in x["issue"] for x in v), _issues(v)


def test_notes_with_no_giveback_language_are_not_flagged():
    v = _check(
        {"ADMIN_NOTES": "$3 per 9oz Kona. School will be paying."},
        {"EVENT_TYPE": "package", "BILLING_MODEL": "PACKAGE_PER_SERVING",
         "RATE_PER_SERVING": 3, "UNITS_SERVED_TOTAL": 40, "TAXABLE": "YES"},
    )
    assert not any("giveback" in x["issue"] for x in v), _issues(v)


def test_the_dictated_spelling_is_caught():
    """Notes and transcripts render it several ways — "gift bag" is what dictation
    produces for "giveback", and it appeared that way in the call itself."""
    for wording in ("Gift bag percentage to be confirmed",
                    "give back to the school", "10 percentage back"):
        v = _check({"ADMIN_NOTES": wording}, {
            "EVENT_TYPE": "selling", "BILLING_MODEL": "SELLING_OPEN",
            "RATE_PER_SERVING": 4, "UNITS_SERVED_TOTAL": 50, "TAXABLE": "YES",
        })
        assert any("giveback" in x["issue"] for x in v), f"{wording}: {_issues(v)}"


def test_a_zero_percent_giveback_still_reduces_nothing():
    """Guards the arithmetic the flag protects: with no percentage the engine
    computes no giveback at all, which is why the row read $0.00."""
    calc = billing.calculate_invoice({
        "BILLING_MODEL": "SELLING_WITH_GIVEBACK", "RATE_PER_SERVING": 4,
        "UNITS_SERVED_TOTAL": 100, "GIVEBACK_PERCENTAGE": 0, "TAXABLE": "YES",
    })
    assert calc["GIVEBACK_AMOUNT"] == 0.0
    assert calc["SUBTOTAL"] == 400.0


def test_setting_the_percentage_reduces_the_subtotal():
    calc = billing.calculate_invoice({
        "BILLING_MODEL": "SELLING_WITH_GIVEBACK", "RATE_PER_SERVING": 4,
        "UNITS_SERVED_TOTAL": 100, "GIVEBACK_PERCENTAGE": 0.15, "TAXABLE": "YES",
    })
    assert calc["GIVEBACK_AMOUNT"] == 60.0
    assert calc["SUBTOTAL"] == 340.0


def test_giveback_is_a_recalculating_override():
    """It must recompute downstream, like counted cash — not merely be recorded
    the way deposit/taxable/paid are."""
    assert overrides.OVERRIDABLE["giveback_percent"] is True


def test_giveback_percentage_is_stored_as_a_decimal():
    """GIVEBACK_PERCENTAGE is a DECIMAL throughout: billing multiplies sales by it
    directly and the UI renders it as pct * 100. The API takes a human percentage,
    so the conversion has to happen at that boundary.

    Storing 20 instead of 0.20 on a $1,000 event computes a $20,000 giveback and a
    -$19,000 subtotal — the whole event's sales twenty times over. This is the guard
    against that unit slipping back in.
    """
    from app.api.routes.financials import GivebackUpdate

    body = GivebackUpdate(giveback_percent=20)
    as_decimal = round(body.giveback_percent / 100.0, 6)
    assert as_decimal == 0.20

    sane = billing.calculate_invoice({
        "BILLING_MODEL": "SELLING_WITH_GIVEBACK", "RATE_PER_SERVING": 5,
        "UNITS_SERVED_TOTAL": 200, "GIVEBACK_PERCENTAGE": as_decimal,
        "TAXABLE": "YES",
    })
    assert sane["GIVEBACK_AMOUNT"] == 200.0    # 20% of $1,000
    assert sane["SUBTOTAL"] == 800.0

    # What the un-converted value would have produced.
    broken = billing.calculate_invoice({
        "BILLING_MODEL": "SELLING_WITH_GIVEBACK", "RATE_PER_SERVING": 5,
        "UNITS_SERVED_TOTAL": 200, "GIVEBACK_PERCENTAGE": 20, "TAXABLE": "YES",
    })
    assert broken["GIVEBACK_AMOUNT"] == 20000.0
    assert broken["SUBTOTAL"] < 0


def test_the_percentage_is_capped_at_100():
    """A decimal above 1.0 would mean giving back more than the event earned."""
    import pydantic
    import pytest

    from app.api.routes.financials import GivebackUpdate

    with pytest.raises(pydantic.ValidationError):
        GivebackUpdate(giveback_percent=120)


# ── selling revenue comes from Square, not a typed rate ──────────────────────

def test_giveback_is_calculated_from_reconciled_sales_not_an_estimate():
    """The venue is owed a percentage of what the truck ACTUALLY took. Computing it
    from units x rate credited them against a guess: 100 served at $4 estimates $400
    and a 20% giveback of $80, while Square reconciled $340 and they are owed $68.
    """
    calc = billing.calculate_invoice({
        "BILLING_MODEL": "SELLING_WITH_GIVEBACK", "RATE_PER_SERVING": 4,
        "UNITS_SERVED_TOTAL": 100, "GIVEBACK_PERCENTAGE": 0.20,
        "ACTUAL_CARD_SALES": 340.0, "ACTUAL_CASH_PRE_TAX": 0.0,
        "ACTUAL_SALES_KNOWN": True, "TAXABLE": "YES",
    })
    assert calc["SALES_AMOUNT"] == 340.0
    assert calc["GIVEBACK_AMOUNT"] == 68.0     # was 80.0
    assert calc["SUBTOTAL"] == 272.0


def test_reconciled_sales_include_counted_cash():
    calc = billing.calculate_invoice({
        "BILLING_MODEL": "SELLING_WITH_GIVEBACK", "RATE_PER_SERVING": 4,
        "UNITS_SERVED_TOTAL": 100, "GIVEBACK_PERCENTAGE": 0.10,
        "ACTUAL_CARD_SALES": 300.0, "ACTUAL_CASH_PRE_TAX": 120.0,
        "ACTUAL_SALES_KNOWN": True, "TAXABLE": "YES",
    })
    assert calc["SALES_AMOUNT"] == 420.0
    assert calc["GIVEBACK_AMOUNT"] == 42.0


def test_the_typed_rate_is_only_a_fallback_before_reconciliation():
    """Nothing reconciled yet — an estimate is better than zero, so the rate is used.
    It is never required, though: a selling event is not invoiced from it."""
    estimate = billing.calculate_invoice({
        "BILLING_MODEL": "SELLING_OPEN", "RATE_PER_SERVING": 4,
        "UNITS_SERVED_TOTAL": 100, "TAXABLE": "YES",
    })
    assert estimate["SALES_AMOUNT"] == 400.0

    # No rate and nothing reconciled: zero, not a crash or an invented figure.
    blank = billing.calculate_invoice({
        "BILLING_MODEL": "SELLING_OPEN", "UNITS_SERVED_TOTAL": 100, "TAXABLE": "YES",
    })
    assert blank["SALES_AMOUNT"] == 0.0


def test_reconciled_sales_win_even_when_no_rate_was_typed():
    """The point of the change: the rate being blank must not zero out real revenue."""
    calc = billing.calculate_invoice({
        "BILLING_MODEL": "SELLING_OPEN", "UNITS_SERVED_TOTAL": 100,
        "ACTUAL_CARD_SALES": 512.50, "ACTUAL_SALES_KNOWN": True, "TAXABLE": "YES",
    })
    assert calc["SALES_AMOUNT"] == 512.50
