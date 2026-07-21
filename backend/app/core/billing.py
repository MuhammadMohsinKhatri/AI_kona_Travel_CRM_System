"""Invoice calculation engine.

Faithful Python port of the n8n "calculations" node (v2.3). Given a classified
event (the flat CLASSIFICATION dict), it computes the invoice amount according to
the resolved BILLING_MODEL.

Key rules preserved from the original:
  * No discount math here — BASE_AMOUNT is already the post-discount quoted price.
  * MG models bill the guaranteed floor + location fee, regardless of servings.
  * Tax is 6% when taxable; a 4% CC/processing fee always applies.
  * If the admin wrote an explicit invoice total in notes (CHECK_INVOICE_AMOUNT),
    that is billing truth and overrides the calculated amount (variance recorded).
"""
from __future__ import annotations

from typing import Any

TAX_RATE = 0.06
CC_FEE_RATE = 0.04  # always applies to invoice/check/card payments

# The 11 billing models the classifier can resolve.
BILLING_MODELS = [
    "INVOICE_PER_SERVING",
    "INVOICE_BASE_FEE_PLUS_SERVINGS",
    "INVOICE_FIXED_PACKAGE",
    "INVOICE_HOURLY",
    "SELLING_OPEN",
    "SELLING_WITH_GIVEBACK",
    "MIN_GUARANTEE_HOURLY",
    "MIN_GUARANTEE_FLAT",
    "HYBRID_HOST_BASE_PLUS_GUEST_EXTRA",
    "HYBRID_HOST_SUBSIDY_PLUS_GUEST_PAYMENT",
    "HYBRID_SELLING_PLUS_MIN_GUARANTEE",
]


def _num(v: Any) -> float:
    try:
        n = float(v)
        return 0.0 if n != n else n  # NaN guard
    except (TypeError, ValueError):
        return 0.0


def _r2(v: float) -> float:
    return round(v + 0.0, 2)


def calculate_invoice(event: dict[str, Any], waive_cc_fee: bool = False) -> dict[str, Any]:
    """Return the calculations block for one classified event.

    ``waive_cc_fee`` forces the 4% processing fee to 0 — used when a check
    payment is confirmed after drafting (client deducts the fee themselves).
    """

    # ── INPUTS ────────────────────────────────────────────────────────────────
    units_total = _num(event.get("UNITS_SERVED_TOTAL"))
    units_included = _num(event.get("UNITS_INCLUDED_IN_BASE"))
    rate_per_serving = _num(event.get("RATE_PER_SERVING"))

    base_amount = _num(event.get("BASE_AMOUNT"))
    location_fee = _num(event.get("LOCATION_FEE"))
    deposit_amount = _num(event.get("DEPOSIT_AMOUNT"))

    # Flat add-on / extra charge (e.g. "$25 for ice cream") — a taxable line
    # item added on top of whatever the billing model produces.
    addon_amount = _num(event.get("ADDON_AMOUNT"))
    addon_label = str(event.get("ADDON_LABEL") or "").strip()

    # When the quoted prices are already tax + fee inclusive ("$310 all-in"),
    # no tax or processing fee is layered on top.
    price_is_all_in = str(event.get("PRICE_IS_ALL_IN") or "").upper() in ("TRUE", "YES", "1")

    # Extracted for audit only — never re-applied (BASE_AMOUNT is post-discount).
    discount_percent = _num(event.get("DISCOUNT_PERCENT"))
    discount_amount = _num(event.get("DISCOUNT_AMOUNT"))

    hourly_rate = _num(event.get("HOURLY_RATE"))
    total_hours = _num(event.get("TOTAL_EVENT_HOURS"))

    min_hourly = _num(event.get("MINIMUM_AMOUNT_PER_HOUR"))
    min_flat = _num(event.get("MINIMUM_FLAT_AMOUNT"))

    giveback_pct = _num(event.get("GIVEBACK_PERCENTAGE"))

    host_subsidy = _num(event.get("HOST_SUBSIDY_PER_SERVING"))
    guest_rate = _num(event.get("GUEST_RATE_PER_SERVING"))

    # AI-extracted from notes if the driver wrote it explicitly. Passed through.
    ai_mg_shortfall = _num(event.get("MG_SHORTFALL"))

    billing_model = str(event.get("BILLING_MODEL") or "").upper().strip()
    payment_method = str(event.get("PAYMENT_METHOD") or "CHECK").upper().strip()

    taxable = str(event.get("TAXABLE") or "YES").upper() == "YES"
    is_paid = str(event.get("PAID_STATUS") or "").upper() in ("TRUE", "PAID", "YES")

    base_is_fixed = str(
        event.get("BASE_IS_FIXED_COMMITMENT", "TRUE")
    ).upper() != "FALSE"

    ai_extracted_invoice = _num(event.get("CHECK_INVOICE_AMOUNT"))
    driver_cash = _num(event.get("CASH_COLLECTED_AMOUNT"))

    # ── CALC VARIABLES ──────────────────────────────────────────────────────────
    subtotal = 0.0
    sales_amount = 0.0
    unit_revenue = 0.0
    hourly_revenue = 0.0
    overage_units = 0.0
    overage_revenue = 0.0
    minimum_required = 0.0
    giveback_amount = 0.0
    host_amount = 0.0
    guest_amount = 0.0

    # ── BILLING LOGIC ─────────────────────────────────────────────────────────
    if billing_model == "INVOICE_PER_SERVING":
        unit_revenue = units_total * rate_per_serving
        subtotal = unit_revenue + location_fee

    elif billing_model == "INVOICE_BASE_FEE_PLUS_SERVINGS":
        unit_revenue = units_total * rate_per_serving
        subtotal = base_amount + unit_revenue + location_fee

    elif billing_model == "INVOICE_FIXED_PACKAGE":
        overage_units = max(0.0, units_total - units_included)
        overage_revenue = overage_units * rate_per_serving  # 0 if rate unknown
        subtotal = base_amount + overage_revenue + location_fee

    elif billing_model == "INVOICE_HOURLY":
        hourly_revenue = total_hours * hourly_rate  # hoisted var, set only here
        # When the hourly rate includes a serving allowance ("$295/hr, each hour
        # includes up to 60 Konas, $4 each additional"), only servings ABOVE
        # UNITS_INCLUDED_IN_BASE are billed per serving. With no allowance
        # (units_included = 0) every served unit is billed, as before.
        overage_units = max(0.0, units_total - units_included)
        overage_revenue = overage_units * rate_per_serving
        subtotal = hourly_revenue + overage_revenue + location_fee

    elif billing_model == "SELLING_OPEN":
        sales_amount = units_total * rate_per_serving
        subtotal = sales_amount + location_fee

    elif billing_model == "SELLING_WITH_GIVEBACK":
        sales_amount = units_total * rate_per_serving
        giveback_amount = sales_amount * giveback_pct
        subtotal = (sales_amount - giveback_amount) + location_fee

    elif billing_model == "MIN_GUARANTEE_HOURLY":
        minimum_required = total_hours * min_hourly
        subtotal = minimum_required + location_fee

    elif billing_model == "MIN_GUARANTEE_FLAT":
        minimum_required = min_flat
        subtotal = minimum_required + location_fee

    elif billing_model == "HYBRID_HOST_BASE_PLUS_GUEST_EXTRA":
        overage_units = max(0.0, units_total - units_included)
        overage_revenue = overage_units * rate_per_serving
        if base_is_fixed:
            subtotal = base_amount + overage_revenue + location_fee
        else:
            billable_units = min(units_total, units_included)
            subtotal = (billable_units * rate_per_serving) + overage_revenue + location_fee

    elif billing_model == "HYBRID_HOST_SUBSIDY_PLUS_GUEST_PAYMENT":
        host_amount = units_total * host_subsidy
        guest_amount = units_total * guest_rate
        subtotal = host_amount + guest_amount + location_fee

    elif billing_model == "HYBRID_SELLING_PLUS_MIN_GUARANTEE":
        minimum_required = total_hours * min_hourly if min_hourly > 0 else min_flat
        subtotal = minimum_required + location_fee

    else:  # UNDEFINED / unrecognized
        subtotal = base_amount + location_fee

    # Flat add-on / extra charge applies to every model.
    subtotal += addon_amount

    # ── TAX + PROCESSING FEE ────────────────────────────────────────────────────
    # The 4% fee applies by default (payment method is rarely known upfront;
    # check-payers deduct it themselves). It is skipped only when the notes
    # already CONFIRM a check payment, or when explicitly waived after a
    # check arrives (waive_cc_fee). When PRICE_IS_ALL_IN, the quoted prices
    # already include tax + fee, so neither is layered on top.
    tax_rate = 0.0 if price_is_all_in else (TAX_RATE if taxable else 0.0)
    confirmed_check_payment = payment_method == "CHECK" and is_paid
    cc_fee_applies = not (price_is_all_in or waive_cc_fee or confirmed_check_payment)
    cc_fee_rate = CC_FEE_RATE if cc_fee_applies else 0.0
    sales_tax = _r2(subtotal * tax_rate)
    cc_fee = _r2(subtotal * cc_fee_rate)
    calculated_invoice_amount = _r2(subtotal + sales_tax + cc_fee)

    # ── FINAL INVOICE AMOUNT ────────────────────────────────────────────────────
    final_invoice_amount = (
        ai_extracted_invoice if ai_extracted_invoice > 0 else calculated_invoice_amount
    )

    has_variance = (
        ai_extracted_invoice > 0
        and _r2(ai_extracted_invoice - calculated_invoice_amount) != 0
    )
    variance_amount = (
        _r2(ai_extracted_invoice - calculated_invoice_amount)
        if ai_extracted_invoice > 0
        else 0.0
    )

    balance_due = _r2(max(0.0, final_invoice_amount - deposit_amount))

    # ── PAYMENT ROUTING ─────────────────────────────────────────────────────────
    cash_collected_calc = 0.0
    card_collected_calc = 0.0
    check_invoice_amount_calc = 0.0
    if payment_method == "CASH":
        cash_collected_calc = final_invoice_amount
    elif payment_method == "CREDIT_CARD":
        card_collected_calc = final_invoice_amount
    else:
        check_invoice_amount_calc = final_invoice_amount

    cash_variance = (
        _r2(driver_cash - final_invoice_amount)
        if payment_method == "CASH" and driver_cash > 0
        else 0.0
    )

    return {
        # ── INVOICE AMOUNTS ──
        "FINAL_INVOICE_AMOUNT": final_invoice_amount,
        "CALCULATED_INVOICE_AMOUNT": calculated_invoice_amount,
        "AI_EXTRACTED_INVOICE_AMOUNT": ai_extracted_invoice,
        "HAS_VARIANCE": has_variance,
        "VARIANCE_AMOUNT": variance_amount,
        "CASH_VARIANCE": cash_variance,
        # ── PAYMENT ROUTING ──
        "PAYMENT_METHOD": payment_method,
        "CHECK_INVOICE_AMOUNT_CALC": check_invoice_amount_calc,
        "CASH_COLLECTED_CALC": cash_collected_calc,
        "CARD_COLLECTED_CALC": card_collected_calc,
        # ── BREAKDOWN ──
        "SUBTOTAL": _r2(subtotal),
        "UNIT_REVENUE": _r2(unit_revenue),
        "HOURLY_REVENUE": _r2(hourly_revenue),
        "OVERAGE_UNITS": overage_units,
        "OVERAGE_REVENUE": _r2(overage_revenue),
        "SALES_AMOUNT": _r2(sales_amount),
        "GIVEBACK_AMOUNT": _r2(giveback_amount),
        "HOST_AMOUNT": _r2(host_amount),
        "GUEST_AMOUNT": _r2(guest_amount),
        "MINIMUM_REQUIRED": _r2(minimum_required),
        "MG_SHORTFALL": _r2(ai_mg_shortfall),
        # ── ADD-ON / ALL-IN ──
        "ADDON_AMOUNT": _r2(addon_amount),
        "ADDON_LABEL": addon_label,
        "PRICE_IS_ALL_IN": price_is_all_in,
        # ── TAX & FEES ──
        "TAX_RATE": tax_rate,
        "SALES_TAX": sales_tax,
        "CC_FEE_RATE": cc_fee_rate,
        "CC_FEE": cc_fee,
        "CC_FEE_WAIVED": not cc_fee_applies,
        "TOTAL_TAX": _r2(sales_tax + cc_fee),
        # ── BALANCE ──
        "BALANCE_DUE": balance_due,
        # ── AUDIT ──
        "DISCOUNT_PERCENT_AUDIT": discount_percent,
        "DISCOUNT_AMOUNT_AUDIT": discount_amount,
        # ── FLAGS ──
        "_flags": {
            "taxable": taxable,
            "isPaid": is_paid,
            "billingModel": billing_model,
            "baseIsFixed": base_is_fixed,
            "paymentMethod": payment_method,
        },
    }
