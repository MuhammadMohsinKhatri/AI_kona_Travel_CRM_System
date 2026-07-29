"""The three published packages, priced exactly as the customer flyers state.

Source: Brett's walkthrough of 2026-07-29 plus the three flyers.

  60 minutes — $295   12oz Small 60 incl / $4    16oz Medium 50 / $5    17oz Colour 40 / $6
  45 minutes — $245   12oz Small 45 incl / $4    16oz Medium 39 / $5    17oz Colour 33 / $6
  Party      — $99 + per serving, up to 30 min, $150 minimum per visit
              12oz $4   16oz $5   17oz $6   21oz Large $7   (nothing included)

The included counts are the whole reason the booking form has a size dropdown:
they are not derivable from the price, and Brett was explicit that the price never
changes — only the count and the overage rate do.
"""
import pytest

from app.core.billing import calculate_invoice
from app.core.rule_classifier import try_rule_classify

SIXTY = {"12oz": (60, 4), "16oz": (50, 5), "17oz": (40, 6)}
FORTY_FIVE = {"12oz": (45, 4), "16oz": (39, 5), "17oz": (33, 6)}
PARTY_RATES = {"12oz": 4, "16oz": 5, "17oz": 6, "21oz": 7}


def _package(base, included, additional, served):
    return calculate_invoice({
        "BILLING_MODEL": "PACKAGE_FIXED", "BASE_AMOUNT": base,
        "UNITS_INCLUDED_IN_BASE": included, "RATE_PER_SERVING": additional,
        "UNITS_SERVED_TOTAL": served, "TAXABLE": "YES",
    })


def _party(rate, served):
    return calculate_invoice({
        "BILLING_MODEL": "PACKAGE_BASE_FEE_PLUS_SERVINGS", "BASE_AMOUNT": 99,
        "RATE_PER_SERVING": rate, "UNITS_SERVED_TOTAL": served,
        "MINIMUM_FLAT_AMOUNT": 150, "TAXABLE": "YES",
    })


# ── Brett's own worked examples, verbatim ────────────────────────────────────

def test_sixty_minute_twelve_ounce_seventy_two_served():
    """"driver shows up, serves 72, $295 covers the first 60, we charge $4 for each
    of the additional 12." """
    assert _package(295, 60, 4, 72)["SUBTOTAL"] == 343.0


def test_sixty_minute_sixteen_ounce_fifty_five_served():
    """"$295 would cover the first 50, and it would be $5 each for the additional
    five in that example." """
    assert _package(295, 50, 5, 55)["SUBTOTAL"] == 320.0


def test_sixty_minute_sixteen_ounce_forty_five_served():
    """"still $295, driver shows up and serves 45 of them" — under the included
    count, so the package price stands and nothing is added."""
    calc = _package(295, 50, 5, 45)
    assert calc["SUBTOTAL"] == 295.0
    assert calc["OVERAGE_UNITS"] == 0


def test_party_package_floors_at_one_hundred_and_fifty():
    """"we only serve, let's say 10 of the $4 ones, that's $40 plus the $99. That
    takes them to $139. We're still going to charge them $150." """
    calc = _party(4, 10)
    assert calc["UNIT_REVENUE"] == 40.0
    assert calc["SUBTOTAL"] == 150.0
    assert calc["MINIMUM_UPLIFT"] == 11.0   # 150 - 139, itemised on the invoice


# ── the full grid ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("size,included,additional", [(k, *v) for k, v in SIXTY.items()])
def test_sixty_minute_grid(size, included, additional):
    """At exactly the included count the price is the package price; one over adds
    that size's rate. The price never varies by size — only the count does."""
    assert _package(295, included, additional, included)["SUBTOTAL"] == 295.0
    assert _package(295, included, additional, included + 1)["SUBTOTAL"] == 295.0 + additional


@pytest.mark.parametrize("size,included,additional", [(k, *v) for k, v in FORTY_FIVE.items()])
def test_forty_five_minute_grid(size, included, additional):
    assert _package(245, included, additional, included)["SUBTOTAL"] == 245.0
    assert _package(245, included, additional, included + 1)["SUBTOTAL"] == 245.0 + additional


@pytest.mark.parametrize("size,rate", PARTY_RATES.items())
def test_party_charges_every_serving(size, rate):
    """Nothing is included on the party package — "$99 for us to show up. Then they
    pay for whatever they want" — so every cup is charged on top."""
    served = 40
    assert _party(rate, served)["SUBTOTAL"] == 99 + served * rate


def test_party_minimum_stops_binding_once_purchases_cover_it():
    # 13 x $4 = 52; 99 + 52 = 151, just past the floor.
    assert _party(4, 13)["SUBTOTAL"] == 151.0
    assert _party(4, 13)["MINIMUM_UPLIFT"] == 0.0


# ── the form's note templates round-trip through the parser ──────────────────

def _cleaned(admin, event_type="Package", served=0, minutes=60):
    end_h, end_m = (14 + minutes // 60), (minutes % 60)
    return {
        "ADMIN_NOTES": admin,
        "EVENT_NOTES_HTML": f"<p>EVENT TYPE: {event_type}<br>ATTENDEES: 80 people</p>",
        "DRIVER_NOTES": f"ACTUAL SERVING COUNT: {served}",
        "EVENT_ID": "x", "EVENT_NAME": "Test", "DATE": "2026-08-01",
        "EVENT_STARTED": "2026-08-01T14:00:00-04:00",
        "EVENT_ENDED": f"2026-08-01T{end_h:02d}:{end_m:02d}:00-04:00",
        "STAFF_ASSIGNED": "A", "EQUIPMENT": "KEV7",
    }


def test_sixty_minute_notes_parse_without_the_llm():
    r = try_rule_classify(_cleaned(
        "$295 covers up to 60 servings, each additional $4 a piece. Send invoice. "
        "Plus tax.", served=72))
    assert r is not None, "fell back to the LLM"
    assert r["BILLING_MODEL"] == "PACKAGE_FIXED"
    assert calculate_invoice(r)["SUBTOTAL"] == 343.0


def test_party_notes_parse_and_carry_the_minimum():
    """The floor must reach the engine as MINIMUM_FLAT_AMOUNT without turning the
    event into a minimum-guarantee model — a minimum on a package is a floor on the
    host's own bill, not a guarantee backstopped by guest sales."""
    r = try_rule_classify(_cleaned(
        "Setup fee $99 plus $4 per serving. Send invoice. Minimum $150. Plus tax.",
        served=10, minutes=30))
    assert r is not None, "fell back to the LLM"
    assert r["BILLING_MODEL"] == "PACKAGE_BASE_FEE_PLUS_SERVINGS"
    assert r["MINIMUM_FLAT_AMOUNT"] == 150.0
    assert calculate_invoice(r)["SUBTOTAL"] == 150.0


def test_a_package_minimum_is_not_confused_with_a_guarantee():
    """"Minimum $150." and "Minimum guarantee $150 flat." are different sentences
    and must stay that way — the second IS a model, the first only sets a floor."""
    floor = try_rule_classify(_cleaned(
        "Setup fee $99 plus $4 per serving. Send invoice. Minimum $150. Plus tax.",
        served=10))
    guarantee = try_rule_classify(_cleaned(
        "Minimum guarantee $150 flat. Host covers shortfall. Plus tax.",
        event_type="Min Guarantee", served=10))
    assert floor["BILLING_MODEL"] == "PACKAGE_BASE_FEE_PLUS_SERVINGS"
    assert guarantee["BILLING_MODEL"] == "MIN_GUARANTEE_FLAT"
