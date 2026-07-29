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
