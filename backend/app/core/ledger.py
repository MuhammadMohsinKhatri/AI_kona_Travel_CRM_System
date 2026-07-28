"""Derived sales columns for the financial ledger.

One implementation, two callers: the pipeline writes these after every run, and
the legacy-sheet importer writes them on import. They used to be two copies of
the same formulas and had already drifted — the importer never recomputed
Sales $ at all — so a fix had to be applied twice and silently wasn't.

The legacy monthly sheet is the authority; these columns exist to reconcile
against it. Formulas, validated below:

    Event Sales - Collected (O) = Square net card + cash pre-tax + Check/Invoice
    Sales Tax Amount        (P) = Square card tax + cash tax
    Sales $                 (Q) = Collected + Sales Tax + card tips
    Net Event Sales         (S) = Collected - giveback

**Check/Invoice is the FULL billed amount** (subtotal + tax + CC fee), not a
pre-tax subtotal — see the validation below. Its tax therefore already sits
inside Collected, which is why Sales Tax Amount stays at-truck-only: breaking
the invoiced tax out again would double-count it.

Check/Invoice is included for **every event type except selling**. For a selling
event the computed invoice figure is a restatement of the same money Square
already collected, and no invoice is ever issued (build_invoice_payload returns
None), so adding it would double the row. There it acts as a *fallback* used
only when the Square reconciliation came back empty — never an addition.

Validated against two real rows:

  Gallery Tower Apartment, 2026-07-25 — card + cash + an invoice, so all three
  money sources at once:
    net card 48.00 | card tax 2.88 | tips 4.00 | cash pre-tax 12.00
    cash tax 0.00  | Check/Invoice 246.03

    Collected  48.00 + 12.00 + 246.03 = 306.03  ✓ sheet: 306.03
    Sales Tax   2.88 +  0.00          =   2.88  ✓ sheet:   2.88
    Sales $   306.03 +  2.88 +   4.00 = 312.91  ✓ sheet: 312.91
    Net       306.03 -  0.00          = 306.03  ✓ sheet: 306.03

  Lincoln Elementary Field Day — subtotal 410.00, Check/Invoice 426.40
  (410 x 1.04). Collected is 426.40, which is what fixes the basis: the billed
  TOTAL, not the 410.00 subtotal.

Both rows also disprove the formula the old code documented for Sales $
("net card + card tax + tips + cash collected" = 66.88 for Gallery Tower, not
312.91) — it left Check/Invoice out, understating Sales $ on every host-billed
event, hybrids included.
"""
from __future__ import annotations


def _r2(v: float) -> float:
    return round((v or 0.0) + 0.0, 2)


def host_billed_applies(event_type: str) -> bool:
    """Does this event type bill the host on top of anything taken at the truck?

    True for invoice, hybrid and minimum-guarantee events: the host owes a base,
    a package, or a shortfall, and that money is separate from guest sales.
    False only for selling, where the truck's own sales ARE the revenue.
    """
    return str(event_type or "").strip().lower() != "selling"


def derive_sales_columns(
    *,
    event_type: str,
    square_net_card: float = 0.0,
    square_card_tax: float = 0.0,
    square_tips_card: float = 0.0,
    cash_pre_tax: float = 0.0,
    cash_tax: float = 0.0,
    billed_amount: float = 0.0,
    giveback_amount: float = 0.0,
) -> dict[str, float]:
    """The four derived sales columns.

    ``billed_amount`` is the Check / Invoice figure — the full amount billed to
    the host, tax and CC fee included. Pass 0 when nothing was billed.
    """
    collected = _r2(square_net_card + cash_pre_tax)
    tax = _r2(square_card_tax + cash_tax)

    if host_billed_applies(event_type):
        collected = _r2(collected + billed_amount)
    elif collected == 0 and billed_amount:
        # Selling with no reconciled Square sales: show the computed sale rather
        # than leaving the row at zero. A fallback, not an addition.
        collected = _r2(billed_amount)

    return {
        "event_sales_collected": collected,
        "sales_tax": tax,
        "sales_dollars": _r2(collected + tax + square_tips_card),
        "net_event_sales": _r2(collected - giveback_amount),
    }
