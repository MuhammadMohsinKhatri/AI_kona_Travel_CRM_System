"""Billing engine tests — mirror the rules in the original n8n calculations node."""
from app.core.billing import calculate_invoice


def test_invoice_per_serving_with_tax_and_ccfee():
    calc = calculate_invoice({
        "BILLING_MODEL": "INVOICE_PER_SERVING",
        "UNITS_SERVED_TOTAL": 100, "RATE_PER_SERVING": 3,
        "TAXABLE": "YES", "PAYMENT_METHOD": "CHECK",
    })
    # subtotal 300; tax 18; cc 12; total 330
    assert calc["SUBTOTAL"] == 300.0
    assert calc["SALES_TAX"] == 18.0
    assert calc["CC_FEE"] == 12.0
    assert calc["FINAL_INVOICE_AMOUNT"] == 330.0


def test_base_fee_plus_servings():
    calc = calculate_invoice({
        "BILLING_MODEL": "INVOICE_BASE_FEE_PLUS_SERVINGS",
        "BASE_AMOUNT": 50, "UNITS_SERVED_TOTAL": 100, "RATE_PER_SERVING": 3,
        "TAXABLE": "NO",
    })
    # subtotal 350; no tax; cc 14; total 364
    assert calc["SUBTOTAL"] == 350.0
    assert calc["SALES_TAX"] == 0.0
    assert calc["FINAL_INVOICE_AMOUNT"] == 364.0


def test_fixed_package_overage_only_when_exceeded():
    calc = calculate_invoice({
        "BILLING_MODEL": "INVOICE_FIXED_PACKAGE",
        "BASE_AMOUNT": 125, "UNITS_INCLUDED_IN_BASE": 40,
        "UNITS_SERVED_TOTAL": 55, "RATE_PER_SERVING": 3, "TAXABLE": "YES",
    })
    # overage 15 * 3 = 45; subtotal 170
    assert calc["OVERAGE_UNITS"] == 15
    assert calc["OVERAGE_REVENUE"] == 45.0
    assert calc["SUBTOTAL"] == 170.0


def test_mg_flat_bills_floor_regardless_of_servings():
    calc = calculate_invoice({
        "BILLING_MODEL": "MIN_GUARANTEE_FLAT",
        "MINIMUM_FLAT_AMOUNT": 500, "UNITS_SERVED_TOTAL": 10, "TAXABLE": "YES",
    })
    assert calc["MINIMUM_REQUIRED"] == 500.0
    assert calc["SUBTOTAL"] == 500.0


def test_ai_extracted_invoice_overrides_and_records_variance():
    calc = calculate_invoice({
        "BILLING_MODEL": "INVOICE_PER_SERVING",
        "UNITS_SERVED_TOTAL": 100, "RATE_PER_SERVING": 3,
        "CHECK_INVOICE_AMOUNT": 400, "TAXABLE": "YES",
    })
    assert calc["FINAL_INVOICE_AMOUNT"] == 400.0
    assert calc["HAS_VARIANCE"] is True
    assert calc["VARIANCE_AMOUNT"] == 70.0  # 400 - 330


def test_selling_with_giveback_deducts_share():
    calc = calculate_invoice({
        "BILLING_MODEL": "SELLING_WITH_GIVEBACK",
        "UNITS_SERVED_TOTAL": 100, "RATE_PER_SERVING": 4,
        "GIVEBACK_PERCENTAGE": 0.2, "TAXABLE": "NO",
    })
    # sales 400; giveback 80; subtotal 320
    assert calc["GIVEBACK_AMOUNT"] == 80.0
    assert calc["SUBTOTAL"] == 320.0


def test_addon_added_as_taxable_amount():
    # Fixed package $442.50 covers 90; plus $25 ice cream add-on.
    calc = calculate_invoice({
        "BILLING_MODEL": "INVOICE_FIXED_PACKAGE",
        "BASE_AMOUNT": 442.50, "UNITS_INCLUDED_IN_BASE": 90,
        "UNITS_SERVED_TOTAL": 90, "ADDON_AMOUNT": 25, "ADDON_LABEL": "Ice cream",
        "TAXABLE": "NO",
    })
    assert calc["ADDON_AMOUNT"] == 25.0
    assert calc["SUBTOTAL"] == 467.50  # 442.50 + 25


def test_price_all_in_suppresses_tax_and_fee():
    # $310 quoted all-in — no 6% tax, no 4% fee layered on top.
    calc = calculate_invoice({
        "BILLING_MODEL": "INVOICE_FIXED_PACKAGE",
        "BASE_AMOUNT": 250, "UNITS_INCLUDED_IN_BASE": 60,
        "UNITS_SERVED_TOTAL": 75, "RATE_PER_SERVING": 4,
        "TAXABLE": "YES", "PRICE_IS_ALL_IN": "TRUE",
    })
    assert calc["SUBTOTAL"] == 310.0  # 250 + 15 over × 4
    assert calc["SALES_TAX"] == 0.0
    assert calc["CC_FEE"] == 0.0
    assert calc["FINAL_INVOICE_AMOUNT"] == 310.0


def test_balance_due_subtracts_deposit():
    calc = calculate_invoice({
        "BILLING_MODEL": "INVOICE_PER_SERVING",
        "UNITS_SERVED_TOTAL": 100, "RATE_PER_SERVING": 3,
        "DEPOSIT_AMOUNT": 100, "TAXABLE": "YES",
    })
    assert calc["BALANCE_DUE"] == 230.0  # 330 - 100
