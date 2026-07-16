"""Rule-based classifier: parses ONLY the New Event form's note templates;
anything free-form must return None so the LLM handles it."""
import os

os.environ.setdefault("CRM_PROVIDER", "mock")

from app.core.billing import calculate_invoice
from app.core.rule_classifier import try_rule_classify


def cleaned(admin, notes_html, driver=""):
    return {
        "ADMIN_NOTES": admin, "EVENT_NOTES_HTML": notes_html, "DRIVER_NOTES": driver,
        "EVENT_ID": "abc123", "EVENT_NAME": "Test", "DATE": "2026-07-15",
        "EVENT_STARTED": "2026-07-15T10:00:00-04:00",
        "EVENT_ENDED": "2026-07-15T12:00:00-04:00",
        "STAFF_ASSIGNED": "Jane Doe", "EQUIPMENT": "KEV7",
    }


def test_fixed_package_under_floor():
    r = try_rule_classify(cleaned(
        "$250 covers up to 60 servings, each additional $4 a piece. Send invoice. Plus tax.",
        "<p>EVENT TYPE: Invoice<br>ATTENDEES: 75 people</p>",
        "ACTUAL SERVING COUNT: 51"))
    assert r is not None and r["BILLING_MODEL"] == "INVOICE_FIXED_PACKAGE"
    assert r["ATTENDEE_COUNT"] == 75 and r["UNITS_SERVED_TOTAL"] == 51
    assert calculate_invoice(r)["FINAL_INVOICE_AMOUNT"] == 275.0


def test_fixed_package_overage():
    r = try_rule_classify(cleaned(
        "$250 covers up to 15 servings, each additional $3 a piece. Send invoice. Plus tax.",
        "<p>EVENT TYPE: Invoice</p>", "ACTUAL SERVING COUNT: 63"))
    assert calculate_invoice(r)["FINAL_INVOICE_AMOUNT"] == 433.40


def test_hourly_addon_exempt_check_paid():
    r = try_rule_classify(cleaned(
        "$295 per hour. Send invoice. Plus $25 for Ice cream. "
        "Client is tax exempt. Card only, no on-site cash.",
        "<p>EVENT TYPE: Invoice</p>", "ACTUAL SERVING COUNT: 40\nPAID: Check"))
    calc = calculate_invoice(r)
    assert calc["FINAL_INVOICE_AMOUNT"] == 615.0  # 2h*295+25, exempt, check→no fee
    assert calc["CC_FEE"] == 0.0


def test_selling_with_giveback():
    r = try_rule_classify(cleaned(
        "Selling event. Giveback percentage: 20%. Plus tax.",
        "<p>EVENT TYPE: Selling</p>"))
    assert r["BILLING_MODEL"] == "SELLING_WITH_GIVEBACK"
    assert r["GIVEBACK_PERCENTAGE"] == 0.20  # calculator multiplies directly


def test_min_guarantee_hourly():
    r = try_rule_classify(cleaned(
        "Minimum guarantee $250 per hour. Host covers shortfall. Plus tax.",
        "<p>EVENT TYPE: Min Guarantee</p>"))
    assert calculate_invoice(r)["SUBTOTAL"] == 500.0  # 2 scheduled hours


def test_hybrid_base_plus_extras():
    r = try_rule_classify(cleaned(
        "Host pays $295 base covering 60 servings. Additional servings $4 billed to host. "
        "Guests pay $4 per serving for extras. Plus tax.",
        "<p>EVENT TYPE: Hybrid</p>", "ACTUAL SERVING COUNT: 75"))
    assert calculate_invoice(r)["SUBTOTAL"] == 355.0


def test_driver_actuals_cash_and_square():
    r = try_rule_classify(cleaned(
        "Open selling event. Guests pay individually. Plus tax.",
        "<p>EVENT TYPE: Selling</p>",
        "PAID: Cash\nCASH COLLECTED: $150\nSQUARE DEVICE: KEV7"))
    assert r["PAYMENT_METHOD"] == "CASH"
    assert r["CASH_COLLECTED_AMOUNT"] == 150.0
    assert r["SQUARE_USED"] == "TRUE" and r["DRIVER_REPORTED_EQUIPMENT"] == "KEV7"


def test_free_text_admin_notes_fall_back_to_ai():
    assert try_rule_classify(cleaned(
        "$245 for 45 minutes, ok to serve everyone with a badge. Plus tax.",
        "<p>EVENT TYPE: Invoice</p>")) is None
    # A known template followed by one unrecognized sentence also falls back.
    assert try_rule_classify(cleaned(
        "$250 covers up to 60 servings, each additional $4 a piece. Send invoice. "
        "Also charge $50 if they run late.",
        "<p>EVENT TYPE: Invoice</p>")) is None


def test_driver_free_text_falls_back_to_ai():
    assert try_rule_classify(cleaned(
        "$295 per hour. Send invoice. Plus tax.", "<p>EVENT TYPE: Invoice</p>",
        "ACTUAL SERVING COUNT: 40\nACTUAL TIMES: ran 1 hr over")) is None


def test_label_model_mismatch_falls_back_to_ai():
    assert try_rule_classify(cleaned(
        "$4 per serving. Send invoice. Plus tax.",
        "<p>EVENT TYPE: Selling</p>")) is None


def test_legacy_unlabeled_notes_fall_back_to_ai():
    assert try_rule_classify(cleaned(
        "$4 per serving. Send invoice. Plus tax.", "<p>bring ice</p>")) is None
