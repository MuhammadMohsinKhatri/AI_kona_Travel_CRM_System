"""Client-requested rules: pending-event gate + CC fee on check payments."""
from app.core.billing import calculate_invoice
from app.core.event_cleaner import is_processable


def test_gate_accepts_confirmed_completed_booked():
    for status in ("Confirmed", "Completed", "Booked"):
        ok, _ = is_processable({"FINAL_EVENT_STATUS": status})
        assert ok, status


def test_gate_accepts_pending_with_asset_and_staff():
    ok, reason = is_processable({
        "FINAL_EVENT_STATUS": "Pending",
        "STAFF_COUNT": 1,
        "EQUIPMENT": "KEV1",
    })
    assert ok
    assert "asset+staff" in reason


def test_gate_rejects_pending_without_assignment():
    ok, reason = is_processable({
        "FINAL_EVENT_STATUS": "Pending", "STAFF_COUNT": 0, "EQUIPMENT": "",
    })
    assert not ok
    assert "no equipment" in reason and "no staff" in reason

    ok, _ = is_processable({
        "FINAL_EVENT_STATUS": "Pending", "STAFF_COUNT": 1, "EQUIPMENT": "",
    })
    assert not ok


def test_gate_rejects_cancelled():
    ok, _ = is_processable({"FINAL_EVENT_STATUS": "Cancelled",
                            "STAFF_COUNT": 2, "EQUIPMENT": "KEV1"})
    assert not ok


BASE = {
    "BILLING_MODEL": "INVOICE_PER_SERVING",
    "UNITS_SERVED_TOTAL": 100, "RATE_PER_SERVING": 3, "TAXABLE": "NO",
}


def test_cc_fee_applies_by_default():
    calc = calculate_invoice({**BASE, "PAYMENT_METHOD": "CHECK"})
    assert calc["CC_FEE"] == 12.0  # method stated but payment NOT confirmed
    assert calc["CC_FEE_WAIVED"] is False


def test_cc_fee_skipped_on_confirmed_check_payment():
    calc = calculate_invoice({**BASE, "PAYMENT_METHOD": "CHECK", "PAID_STATUS": True})
    assert calc["CC_FEE"] == 0.0
    assert calc["CC_FEE_WAIVED"] is True
    assert calc["FINAL_INVOICE_AMOUNT"] == 300.0


def test_cc_fee_manual_waive():
    calc = calculate_invoice({**BASE, "PAYMENT_METHOD": "CHECK"}, waive_cc_fee=True)
    assert calc["CC_FEE"] == 0.0
    assert calc["CC_FEE_WAIVED"] is True


def test_cc_fee_still_applies_for_card():
    calc = calculate_invoice({**BASE, "PAYMENT_METHOD": "CREDIT_CARD", "PAID_STATUS": True})
    assert calc["CC_FEE"] == 12.0
