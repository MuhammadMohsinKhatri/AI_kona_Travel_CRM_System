"""Human/automation overrides on a ledger row, and the recompute they trigger.

Cash is the motivating case. It is counted after the event — by another
automation, or by a person — so the classifier's ``CASH_COLLECTED_AMOUNT``
(scraped from the driver's notes) is unreliable and usually 0. An override
recorded here wins over the classifier and, crucially, survives the next
pipeline run.

Only fields that are genuinely *inputs* belong here. Everything downstream —
cash tax, event sales, sales $ — stays derived, and is recomputed from the
override rather than being separately editable. That keeps one source of
truth: there is no way to save a row whose tax doesn't follow from its cash.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.models import FinancialEntry

# Fields a person or an automation may set. The bool says whether writing it
# recomputes anything: cash does, the rest are stored for now and wired up
# later (deliberately inert so nobody's invoice moves unexpectedly).
OVERRIDABLE: dict[str, bool] = {
    "cash_collected": True,
    "deposit": False,
    "taxable": False,
    "paid": False,
    "payment_method": False,
}

VALID_SOURCES = ("api", "manual")


def _r2(v: float) -> float:
    return round(v + 0.0, 2)


def get_override(entry: FinancialEntry, field: str) -> Optional[Any]:
    """The override for ``field``, or None if the computed value stands."""
    return (entry.overrides or {}).get(field)


def source_of(entry: FinancialEntry, field: str) -> str:
    """Where this field's current value came from: api | manual | ai."""
    meta = (entry.override_meta or {}).get(field) or {}
    return meta.get("source") or "ai"


def set_override(
    entry: FinancialEntry,
    field: str,
    value: Any,
    *,
    source: str,
    by: str = "",
) -> None:
    """Record an override plus who set it and when.

    Reassigns rather than mutating: SQLAlchemy won't detect an in-place change
    to a JSON dict, so mutating would look saved and silently vanish.
    """
    if field not in OVERRIDABLE:
        raise ValueError(f"{field} is not overridable")
    if source not in VALID_SOURCES:
        raise ValueError(f"source must be one of {VALID_SOURCES}")

    entry.overrides = {**(entry.overrides or {}), field: value}
    entry.override_meta = {
        **(entry.override_meta or {}),
        field: {
            "source": source,
            "by": by,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    }


def clear_override(entry: FinancialEntry, field: str) -> None:
    """Hand ``field`` back to the automation."""
    entry.overrides = {k: v for k, v in (entry.overrides or {}).items() if k != field}
    entry.override_meta = {
        k: v for k, v in (entry.override_meta or {}).items() if k != field
    }


def recompute_cash_chain(entry: FinancialEntry) -> dict[str, float]:
    """Recompute every figure that depends on cash, and write them onto ``entry``.

    Mirrors the same arithmetic the pipeline applies when it first builds the
    row (``pipeline._upsert_financial_entry``) — deliberately, so a row settled
    by an override is indistinguishable from one the pipeline produced.

    Invoice-type events are excluded from the at-event sales maths: their
    money is the invoice, and their tax already sits inside it.

    Returns the fields it changed, for the audit trail.
    """
    cash = float(get_override(entry, "cash_collected") or entry.cash_collected or 0.0)
    rate = entry.total_tax_rate or 0.0
    taxable = bool(entry.taxable)

    entry.cash_collected = _r2(cash)
    entry.cash_tax = _r2(cash - cash / (1 + rate)) if (taxable and rate and cash) else 0.0
    entry.cash_pre_tax = _r2(cash - entry.cash_tax) if cash else 0.0

    if str(entry.event_type or "").strip().lower() != "invoice":
        entry.event_sales_collected = _r2(entry.square_net_card + entry.cash_pre_tax)
        entry.sales_dollars = _r2(
            entry.square_net_card
            + entry.square_card_tax
            + entry.square_tips_card
            + cash
        )
        entry.net_event_sales = _r2(entry.event_sales_collected - entry.giveback_amount)
    entry.sales_tax = _r2(entry.square_card_tax + entry.cash_tax)
    entry.actual_sales = entry.square_net_card or cash

    return {
        "cash_collected": entry.cash_collected,
        "cash_tax": entry.cash_tax,
        "cash_pre_tax": entry.cash_pre_tax,
        "event_sales_collected": entry.event_sales_collected,
        "sales_tax": entry.sales_tax,
        "sales_dollars": entry.sales_dollars,
        "net_event_sales": entry.net_event_sales,
    }


# ── Min-guarantee settlement ────────────────────────────────────────────────
# An MG event's invoice is the gap between what the truck actually took and
# the minimum the host guaranteed. Cash is half of "actually took", so this
# can only be decided once cash is in — which is why the nightly run defers
# these events instead of invoicing them on incomplete data.

MG_BILLING_MODELS = (
    "MIN_GUARANTEE_HOURLY",
    "MIN_GUARANTEE_FLAT",
    "HYBRID_SELLING_PLUS_MIN_GUARANTEE",
)


def is_min_guarantee(billing_model: str) -> bool:
    return str(billing_model or "").upper().strip() in MG_BILLING_MODELS


def mg_shortfall(entry: FinancialEntry) -> float:
    """How much the host still owes to reach the guaranteed minimum.

    Sales are compared PRE-TAX on both sides: the minimum is a sales target,
    and sales tax isn't the truck's revenue, so it must not help clear the
    bar (which would also make a taxable event easier to satisfy than an
    exempt one for no defensible reason).

    0.0 means the minimum was met — and therefore no invoice.
    """
    total_sales = (entry.square_net_card or 0.0) + (entry.cash_pre_tax or 0.0)
    return _r2(max(0.0, (entry.minimum_required or 0.0) - total_sales))
