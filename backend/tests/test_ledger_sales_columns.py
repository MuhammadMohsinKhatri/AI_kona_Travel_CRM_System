"""Derived sales columns, checked against real legacy-sheet rows.

The sheet is the authority here: these columns exist to reconcile against it, so
a formula is only correct if it reproduces a real row to the cent.
"""
from app.core.ledger import derive_sales_columns, host_billed_applies


def test_matches_the_gallery_tower_sheet_row():
    """Gallery Tower Apartment, 2026-07-25 — a real row carrying at-truck card
    sales, cash, AND a Check/Invoice amount, so it exercises all three money
    sources at once.

    Sheet values: Collected 306.03 | Tax 2.88 | Sales $ 312.91 | Net 306.03.
    """
    cols = derive_sales_columns(
        event_type="hybrid",
        square_net_card=48.00,
        square_card_tax=2.88,
        square_tips_card=4.00,
        cash_pre_tax=12.00,
        cash_tax=0.00,
        billed_amount=246.03,
        giveback_amount=0.0,
    )
    assert cols["event_sales_collected"] == 306.03
    assert cols["sales_tax"] == 2.88
    assert cols["sales_dollars"] == 312.91
    assert cols["net_event_sales"] == 306.03


def test_billed_amount_is_the_full_invoice_not_the_subtotal():
    """Lincoln Elementary: subtotal 410.00, Check/Invoice 426.40 (410 x 1.04).
    Collected must be the billed TOTAL — this is what fixes the basis, and it is
    why Sales Tax Amount stays at-truck-only (the invoiced tax is already inside
    the billed figure, so breaking it out again would double-count)."""
    cols = derive_sales_columns(event_type="invoice", billed_amount=426.40)
    assert cols["event_sales_collected"] == 426.40   # not 410.00
    assert cols["sales_tax"] == 0.0


def test_old_sales_dollars_formula_would_have_been_wrong():
    """Guards the specific regression: the previously documented formula was
    "net card + card tax + tips + cash collected", giving 66.88 for the Gallery
    Tower row instead of 312.91 — it omitted Check/Invoice entirely."""
    old = 48.00 + 2.88 + 4.00 + 12.00
    assert old == 66.88
    cols = derive_sales_columns(
        event_type="hybrid", square_net_card=48.00, square_card_tax=2.88,
        square_tips_card=4.00, cash_pre_tax=12.00, billed_amount=246.03,
    )
    assert cols["sales_dollars"] == 312.91


def test_hybrid_with_no_at_truck_sales_still_reports_the_invoiced_amount():
    """The reported bug: a hybrid event whose guests bought nothing at the truck
    left Collected, Sales $ and Net Event Sales at 0, because the whole
    non-invoice branch was Square-only with no Check/Invoice term."""
    cols = derive_sales_columns(event_type="hybrid", billed_amount=159.00)
    assert cols["event_sales_collected"] == 159.00  # was 0.00
    assert cols["sales_dollars"] == 159.00          # was 0.00
    assert cols["net_event_sales"] == 159.00        # was 0.00


def test_hybrid_adds_guest_sales_and_the_invoiced_base_together():
    """A hybrid is both: guests pay at the truck AND the host is billed a base.
    The row must carry both, not one or the other."""
    cols = derive_sales_columns(
        event_type="hybrid", square_net_card=92.00, square_card_tax=5.52,
        billed_amount=159.00,
    )
    assert cols["event_sales_collected"] == 251.00  # 92 + 159
    assert cols["sales_tax"] == 5.52
    assert cols["sales_dollars"] == 256.52


def test_sales_dollars_is_collected_plus_tax_plus_tips():
    """The reconciliation property, held for every row."""
    cols = derive_sales_columns(
        event_type="invoice", square_net_card=100.0, square_card_tax=6.0,
        square_tips_card=5.0, cash_pre_tax=50.0, cash_tax=3.0,
        billed_amount=212.0,
    )
    assert cols["sales_dollars"] == round(
        cols["event_sales_collected"] + cols["sales_tax"] + 5.0, 2
    )


def test_selling_uses_the_computed_sale_as_fallback_not_an_addition():
    """A selling event's computed invoice figure restates the same money Square
    already collected, so adding it would double the row."""
    assert host_billed_applies("selling") is False

    reconciled = derive_sales_columns(
        event_type="selling", square_net_card=300.0, square_card_tax=18.0,
        billed_amount=318.0,
    )
    assert reconciled["event_sales_collected"] == 300.0  # not 618.0

    empty = derive_sales_columns(event_type="selling", billed_amount=318.0)
    assert empty["event_sales_collected"] == 318.0


def test_giveback_reduces_only_net_event_sales():
    cols = derive_sales_columns(
        event_type="selling", square_net_card=500.0, square_card_tax=30.0,
        giveback_amount=100.0,
    )
    assert cols["event_sales_collected"] == 500.0
    assert cols["net_event_sales"] == 400.0


def test_minimum_guarantee_counts_the_shortfall_as_host_billed():
    """An MG host owes the gap to the minimum — money separate from guest sales,
    so it belongs in the row alongside them."""
    assert host_billed_applies("minimum guarantee") is True
    cols = derive_sales_columns(
        event_type="minimum guarantee", square_net_card=400.0,
        square_card_tax=24.0, billed_amount=106.0,
    )
    assert cols["event_sales_collected"] == 506.0
