"""Working out what a received check settles, before anything is written.

Given a check we have read (payer, amount) and the open invoices KonaOS holds,
this decides which invoice it pays and what should change. It writes nothing —
the caller shows the plan, a person agrees, and only then does it happen.

Two departures from the n8n workflow this replaces, both deliberate:

**Matched on payer + amount, not on date.** The workflow searched events by the
CHECK's date, which only works because checks usually arrive near the event.
They don't always: a school pays in three weeks and the search window misses the
event entirely. An invoice already carries the payer's business name and its
grand total, and a check carries the same two facts — so match those directly and
take the event from the invoice. No date guessing, and it lands on the invoice we
actually need rather than reaching it through the event.

**A check means the 4% processing fee comes off.** Card processing is what the
fee pays for; a paper check doesn't incur it, so the invoice is recomputed
without it and the client owes the smaller figure. That is the rule the office
already applies by hand, and it is why the check total legitimately differs from
the invoice total — treating that gap as an underpayment would chase a customer
who paid exactly right.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.event_matcher import _significant, _tokens

# Checks and invoices are compared to the cent, but a stated figure can arrive a
# hair off through rounding.
_TOL = 0.02

# An amount matching the invoice to the cent is the strongest signal a check
# carries — stronger than the name, which arrives via OCR of handwriting.
_EXACT_AMOUNT_POINTS = 60
_NEAR_AMOUNT_POINTS = 25
_NAME_ALL_TOKENS = 40
_NAME_MOST_TOKENS = 25
_NAME_SOME_TOKENS = 10

# Below this, we are guessing. A wrong match marks another customer's invoice
# paid, so the floor is deliberately high.
MIN_CONFIDENT_SCORE = 50
AMBIGUITY_MARGIN = 15

# Statuses meaning "nothing left to collect". A check should never re-settle one.
_SETTLED = ("paid", "void", "cancelled", "canceled", "refunded")

_MONEY_RE = re.compile(r"[^\d.\-]")


def _num(v: Any) -> float:
    try:
        n = float(_MONEY_RE.sub("", str(v or "")) or 0)
        return 0.0 if n != n else n
    except (TypeError, ValueError):
        return 0.0


def _r2(v: float) -> float:
    return round(v + 0.0, 2)


def _name_points(payer: str, business: str) -> tuple[int, str]:
    """How much the check's payer looks like the invoice's business name."""
    p_tokens = _significant(_tokens(payer))
    b_tokens = _tokens(business)
    if not p_tokens or not b_tokens:
        return 0, "name+0"
    hits = sum(1 for t in p_tokens if any(t == bt or t in bt for bt in b_tokens))
    if hits == len(p_tokens):
        return _NAME_ALL_TOKENS, "name+40 all tokens"
    if hits / len(p_tokens) > 0.5:
        return _NAME_MOST_TOKENS, "name+25 most tokens"
    if hits:
        return _NAME_SOME_TOKENS, "name+10 some tokens"
    return 0, "name+0"


def is_settled(invoice: dict[str, Any]) -> bool:
    status = str(invoice.get("invoiceStatus") or invoice.get("status") or "").lower()
    if any(s in status for s in _SETTLED):
        return True
    return bool(invoice.get("manuallyMarkedAsPaid"))


@dataclass
class InvoiceCandidate:
    invoice: dict[str, Any]
    score: int
    flags: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return str(self.invoice.get("id") or "")

    @property
    def number(self) -> str:
        return str(self.invoice.get("invoiceNumber") or "")

    @property
    def business(self) -> str:
        return str(self.invoice.get("businessName") or "")

    @property
    def total(self) -> float:
        return _num(self.invoice.get("grandTotal"))


@dataclass
class InvoiceMatch:
    invoice: Optional[dict[str, Any]]
    reason: str
    candidates: list[InvoiceCandidate] = field(default_factory=list)
    needs_choice: bool = False

    @property
    def invoice_id(self) -> str:
        return str((self.invoice or {}).get("id") or "")


def match_invoice(
    invoices: list[dict[str, Any]],
    payer_name: str,
    amount: float,
    *,
    without_fee_amounts: Optional[dict[str, float]] = None,
) -> InvoiceMatch:
    """Which open invoice this check pays.

    ``without_fee_amounts`` maps invoice id to what that invoice would total with
    the 4% processing fee removed. A check written against the fee-free figure is
    the NORMAL case — the office quotes it that way — so it has to score as an
    exact amount match, otherwise every correctly-written check looks like an
    underpayment and matches nothing.
    """
    without_fee = without_fee_amounts or {}
    candidates: list[InvoiceCandidate] = []

    for inv in invoices:
        if is_settled(inv):
            continue
        flags: list[str] = []
        score = 0

        total = _num(inv.get("grandTotal"))
        fee_free = without_fee.get(str(inv.get("id") or ""))
        if total > 0 and abs(total - amount) <= _TOL:
            score += _EXACT_AMOUNT_POINTS
            flags.append("amount+60 exact")
        elif fee_free is not None and abs(fee_free - amount) <= _TOL:
            score += _EXACT_AMOUNT_POINTS
            flags.append("amount+60 exact less the 4% fee")
        elif total > 0 and abs(total - amount) <= max(1.0, total * 0.05):
            score += _NEAR_AMOUNT_POINTS
            flags.append("amount+25 within 5%")
        else:
            flags.append("amount+0")

        name_pts, name_flag = _name_points(payer_name, inv.get("businessName", ""))
        score += name_pts
        flags.append(name_flag)

        candidates.append(InvoiceCandidate(invoice=inv, score=score, flags=flags))

    candidates.sort(key=lambda c: c.score, reverse=True)

    if not candidates:
        return InvoiceMatch(
            None, "There are no unpaid invoices to match this check against.", []
        )

    best = candidates[0]
    if best.score < MIN_CONFIDENT_SCORE:
        return InvoiceMatch(
            None,
            f"No unpaid invoice confidently matches \"{payer_name}\" for "
            f"${amount:,.2f}. Pick one below, or check the amount was read right.",
            candidates[:5],
            needs_choice=True,
        )

    runner_up = candidates[1] if len(candidates) > 1 else None
    if runner_up and best.score - runner_up.score < AMBIGUITY_MARGIN:
        return InvoiceMatch(
            None,
            f"Invoices {best.number or best.id} and "
            f"{runner_up.number or runner_up.id} match this check about equally "
            "well. Choose which one it pays.",
            candidates[:5],
            needs_choice=True,
        )

    return InvoiceMatch(
        best.invoice,
        f"Matched invoice {best.number or best.id} for {best.business} "
        f"({', '.join(best.flags)}).",
        candidates[:5],
    )


@dataclass
class SettlePlan:
    """Exactly what applying this check will change. Nothing has happened yet."""

    invoice_id: str = ""
    invoice_number: str = ""
    event_id: str = ""
    business_name: str = ""

    check_amount: float = 0.0
    invoice_total: float = 0.0          # as KonaOS holds it now, fee included
    cc_fee_removed: float = 0.0         # the 4% that comes off for a check
    amount_due_after_fee: float = 0.0   # what the client actually owes

    variance: float = 0.0               # check − amount_due_after_fee
    status: str = "exact"               # exact | underpaid | overpaid
    fully_paid: bool = True

    payment_method: str = "CHECK"
    warnings: list[str] = field(default_factory=list)

    @property
    def settles_cleanly(self) -> bool:
        return self.status == "exact"


def build_settle_plan(
    invoice: dict[str, Any],
    check_amount: float,
    *,
    calc: Optional[dict[str, Any]] = None,
    fee_free_total: Optional[float] = None,
) -> SettlePlan:
    """What this check does to this invoice, with the processing fee taken off.

    ``fee_free_total`` is the recomputed total without the 4% (billing.
    calculate_invoice with waive_cc_fee=True). Passed in rather than derived by
    subtracting 4% here: the fee is computed on the pre-tax subtotal, so peeling
    it off arithmetically drifts by a cent or two, and a cent of drift is what
    turns "paid in full" into "underpaid by $0.01" and puts an event on Needs
    Attention for nothing.
    """
    plan = SettlePlan(
        invoice_id=str(invoice.get("id") or ""),
        invoice_number=str(invoice.get("invoiceNumber") or ""),
        event_id=str(invoice.get("eventId") or ""),
        business_name=str(invoice.get("businessName") or ""),
        check_amount=_r2(check_amount),
        invoice_total=_r2(_num(invoice.get("grandTotal"))),
    )

    if fee_free_total is None and calc:
        fee_free_total = _num(calc.get("FINAL_INVOICE_AMOUNT")) - _num(calc.get("CC_FEE"))
    if fee_free_total is None:
        fee_free_total = plan.invoice_total
        plan.warnings.append(
            "Couldn't recompute the invoice without the 4% fee, so the figure "
            "below still includes it — check before applying."
        )

    plan.amount_due_after_fee = _r2(fee_free_total)
    plan.cc_fee_removed = _r2(max(0.0, plan.invoice_total - plan.amount_due_after_fee))

    plan.variance = _r2(plan.check_amount - plan.amount_due_after_fee)
    if abs(plan.variance) <= _TOL:
        plan.status, plan.fully_paid = "exact", True
    elif plan.variance < 0:
        plan.status, plan.fully_paid = "underpaid", False
        plan.warnings.append(
            f"The check is ${abs(plan.variance):,.2f} short of the "
            f"${plan.amount_due_after_fee:,.2f} due. Recording it as a part "
            "payment, so the balance stays open."
        )
    else:
        plan.status, plan.fully_paid = "overpaid", True
        plan.warnings.append(
            f"The check is ${plan.variance:,.2f} more than the "
            f"${plan.amount_due_after_fee:,.2f} due."
        )

    if plan.cc_fee_removed <= 0:
        plan.warnings.append(
            "This invoice carried no 4% processing fee, so nothing was removed."
        )
    if not plan.event_id:
        plan.warnings.append(
            "This invoice isn't linked to an event in KonaOS, so the event's own "
            "payment fields can't be updated."
        )
    return plan
