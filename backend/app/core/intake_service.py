"""Turning a photographed check or a dictated cash total into a reviewable plan,
and — once a person agrees — into the writes that settle it.

This is the join between the fallible half (``intake_readers``: vision and
speech) and the exact half (``event_matcher``, ``check_settlement``,
``billing``). It is deliberately split in two everywhere:

  * ``review_*`` reads, matches and computes. It writes NOTHING, so it can be
    called on every upload, re-called after a person edits a misread amount, and
    called again on the way in to Apply without consequence.
  * ``apply_*`` performs the writes, and only ever for one plan a person has
    seen. It re-reads the invoice first — the review may be minutes old, and in
    that time the same check can have been keyed in by hand.

The review step is the safety model. A misread amount that goes straight to
KonaOS is a payment recorded against the wrong customer; the same misread amount
on a review screen is a two-second typo. Nothing here decides on its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core import billing, invoice_builder
from app.core.check_settlement import (InvoiceMatch, SettlePlan,
                                       build_settle_plan, is_settled,
                                       match_invoice)
from app.core.event_matcher import Candidate, MatchResult, match_event
from app.core.intake_readers import CashEntry, CheckRead
from app.models import Event, FinancialEntry, Invoice

# How far back to look for the event a spoken phrase means, when the speaker
# didn't say a date. Wide enough for "I never got round to Friday's", narrow
# enough that a weekly booking at the same school doesn't put two identical
# candidates in front of the matcher every time.
CASH_LOOKBACK_DAYS = 7


def _r2(v: float) -> float:
    return round(float(v or 0) + 0.0, 2)


# ── checks ───────────────────────────────────────────────────────────────────


def _local_invoice(db: Session, crm_invoice_id: str) -> Optional[Invoice]:
    if not crm_invoice_id:
        return None
    return (
        db.query(Invoice)
        .filter(Invoice.crm_invoice_id == crm_invoice_id)
        .order_by(Invoice.id.desc())
        .first()
    )


def fee_free_total(db: Session, crm_invoice_id: str) -> Optional[float]:
    """What this invoice comes to once the 4% processing fee is taken off.

    Computed by re-running the billing engine with ``waive_cc_fee=True``, never
    by subtracting 4% from the grand total. The fee is charged on the PRE-TAX
    subtotal, so peeling it off arithmetically drifts a cent or two — and a cent
    of drift is exactly what turns "paid in full" into "underpaid by $0.01" and
    parks the event on Needs Attention for nothing.

    None when there is no figure we can stand behind, and the caller then says
    so rather than inventing one. That happens in two ways:

      * we hold no local record of the invoice (drafted outside this system, or
        the event has since been deleted); or
      * the invoice total came from an amount stated in the notes rather than
        from the engine. A stated "$136.40" wins over the calculation, so
        re-running with the fee waived returns the same number — and how much of
        somebody's typed total was processing fee is not knowable. Reporting
        "no fee to remove" there would be a guess dressed as arithmetic.
    """
    invoice = _local_invoice(db, crm_invoice_id)
    if invoice is None:
        return None
    event = db.get(Event, invoice.event_id)
    if event is None or not event.classification:
        return None
    calc = billing.calculate_invoice(event.classification, waive_cc_fee=True)
    if float(calc.get("AI_EXTRACTED_INVOICE_AMOUNT") or 0) > 0:
        return None
    total = float(calc.get("FINAL_INVOICE_AMOUNT") or 0)
    return _r2(total) if total > 0 else None


def fee_free_totals(db: Session, invoices: list[dict[str, Any]]) -> dict[str, float]:
    """The fee-free figure for every open invoice, keyed by CRM invoice id.

    Handed to the matcher so a check written for the fee-free amount — the
    NORMAL case, since the office quotes that figure — scores as an exact match
    instead of looking like an underpayment and matching nothing.
    """
    out: dict[str, float] = {}
    for inv in invoices:
        if is_settled(inv):
            continue
        inv_id = str(inv.get("id") or "")
        total = fee_free_total(db, inv_id)
        if total is not None:
            out[inv_id] = total
    return out


@dataclass
class CheckReview:
    """One check, read and matched, with nothing written yet."""

    check: CheckRead
    match: InvoiceMatch
    plan: Optional[SettlePlan] = None

    @property
    def ready(self) -> bool:
        """Whether Apply has something unambiguous to do."""
        return self.plan is not None and not self.match.needs_choice


def review_check(
    db: Session,
    crm,
    check: CheckRead,
    *,
    invoice_id: str = "",
) -> CheckReview:
    """Which invoice this check pays, and what applying it would change.

    ``invoice_id`` overrides the matcher — that is the reviewer picking one of
    the near-misses off the screen, which must always beat the score.
    """
    # An amount alone is enough to try. A grand total matching an open invoice
    # to the cent is the strongest signal a check carries — stronger than a name
    # read out of handwriting — and where two invoices share a total the
    # ambiguity guard stops rather than picks. Refusing to match without a payer
    # name threw away readable checks whose only unreadable field was the one we
    # needed least.
    if check.amount <= 0 and not check.payer_name.strip() and not invoice_id:
        return CheckReview(
            check=check,
            match=InvoiceMatch(
                None,
                check.error or "Nothing could be read off that image — no payer "
                               "and no amount. Try a clearer photo, or type them in.",
                [],
                needs_choice=True,
            ),
        )

    invoices = crm.list_invoices() or []
    without_fee = fee_free_totals(db, invoices)

    if invoice_id:
        chosen = next((i for i in invoices if str(i.get("id") or "") == invoice_id), None)
        if chosen is None:
            return CheckReview(
                check=check,
                match=InvoiceMatch(
                    None,
                    f"Invoice {invoice_id} is no longer in the open-invoice list "
                    "— it may have just been paid. Reload and try again.",
                    [], needs_choice=True,
                ),
            )
        match = InvoiceMatch(chosen, "Invoice chosen by hand.")
    else:
        match = match_invoice(
            invoices, check.payer_name, check.amount,
            without_fee_amounts=without_fee,
        )

    if match.invoice is None:
        return CheckReview(check=check, match=match)

    plan = build_settle_plan(
        match.invoice,
        check.amount,
        fee_free_total=without_fee.get(match.invoice_id),
    )
    plan.payment_method = "CHECK"
    return CheckReview(check=check, match=match, plan=plan)


def auto_applicable_check(review: CheckReview) -> tuple[bool, str]:
    """Whether this check may settle itself, with nobody looking.

    The photograph is read by a model, so what makes this safe is not the
    model's own confidence — it is corroboration. An amount that matches an open
    invoice's total to the cent, on the only invoice that matches, is evidence no
    misreading survives: a wrong amount lands on nothing, and a total two
    customers share is caught by the ambiguity guard instead of picked.

    So the bar is the arithmetic agreeing exactly. A check that is short or over
    might genuinely be a part payment — or might be a 3 read as an 8, which is
    the same thing on screen and a very different thing in the ledger. Those come
    back to a person. Everything else applies on upload.
    """
    if review.plan is None or review.match.needs_choice:
        return False, review.match.reason
    if review.plan.status != "exact":
        return False, (
            f"The check is {'short of' if review.plan.variance < 0 else 'more than'} "
            f"the amount due, so it hasn't been recorded — check the amount was "
            f"read right, then apply it below."
        )
    if review.plan.warnings:
        # The fee couldn't be recomputed, or the invoice isn't linked to an
        # event. Both change what applying MEANS, so a person should see it.
        return False, review.plan.warnings[0]
    return True, ""


def auto_applicable_cash(review: CashReview) -> tuple[bool, str]:
    """Whether this spoken line may post itself.

    Cash has no second figure to check itself against — nothing corroborates
    "seven bucks" the way an invoice total corroborates a check. What carries it
    is that the event matched unambiguously and the amount is a plain overwrite
    of a field a person can see and change on Event Financials afterwards.
    """
    if not review.ready:
        return False, review.blocked or review.match.reason
    return True, ""


def _fee_free_payload(db: Session, invoice: Invoice) -> Optional[dict[str, Any]]:
    """The invoice as it should read once the 4% comes off, ready to PUT.

    Rebuilt through the same builder the draft came from rather than edited by
    hand, so the fee line disappears, the subtotal and tax stay whatever the
    engine says they are, and the document a client opens is one this system
    knows how to produce. ``id`` is carried across so KonaOS updates the
    existing invoice — delete-and-recreate would drop the invoice number and
    open a window where the delete has run and the create hasn't.
    """
    event = db.get(Event, invoice.event_id)
    if event is None or not event.classification:
        return None
    calc = billing.calculate_invoice(event.classification, waive_cc_fee=True)
    payload = invoice_builder.build_invoice_payload(
        {**event.classification, "calculations": calc},
        event.cleaned or {},
        event.raw or {},
    )
    if not payload:
        return None
    payload["id"] = invoice.crm_invoice_id
    # Keep the number the client already has on the draft they were sent.
    if invoice.invoice_number:
        payload["invoiceNumber"] = invoice.invoice_number
    # Carry the draft's own status and draft flag across untouched. Taking a fee
    # off is a change to the figures, not a decision to issue the document —
    # flipping saveAsDraft here would send a client an invoice nobody asked to
    # send. Marking it paid is a separate, explicit call.
    for kept in ("saveAsDraft", "invoiceStatus"):
        if kept in (invoice.payload or {}):
            payload[kept] = (invoice.payload or {})[kept]
    return payload


@dataclass
class ApplyResult:
    """What actually happened, per item, in the words the reviewer will read."""

    ok: bool
    summary: str
    invoice_id: str = ""
    crm_event_id: str = ""
    dry_run: bool = False
    warnings: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)


def apply_check(
    db: Session,
    crm,
    plan: SettlePlan,
    *,
    by: str = "",
    dry_run: bool = False,
) -> ApplyResult:
    """Take the 4% off the invoice, then record the check against it.

    Re-reads the invoice from KonaOS first. The plan a person approved was
    computed when they uploaded the photo, and a check keyed in by hand in the
    meantime must not be recorded twice.
    """
    from app.models import CrmAuditEntry

    invoices = crm.list_invoices() or []
    current = next(
        (i for i in invoices if str(i.get("id") or "") == plan.invoice_id), None
    )
    if current is None:
        return ApplyResult(
            False,
            f"Invoice {plan.invoice_number or plan.invoice_id} is no longer open "
            "in KonaOS — nothing was changed.",
            invoice_id=plan.invoice_id,
        )
    if is_settled(current):
        return ApplyResult(
            False,
            f"Invoice {plan.invoice_number or plan.invoice_id} has already been "
            "marked paid in KonaOS. Nothing was changed.",
            invoice_id=plan.invoice_id,
        )

    warnings = list(plan.warnings)
    local = _local_invoice(db, plan.invoice_id)

    if dry_run:
        return ApplyResult(
            True,
            f"Dry run — would take ${plan.cc_fee_removed:,.2f} off invoice "
            f"{plan.invoice_number or plan.invoice_id} and record "
            f"${plan.check_amount:,.2f}. Nothing was written.",
            invoice_id=plan.invoice_id,
            crm_event_id=plan.event_id,
            dry_run=True,
            warnings=warnings,
        )

    # 1. The invoice itself, without the processing fee.
    fee_removed = False
    if local is not None and plan.cc_fee_removed > 0:
        payload = _fee_free_payload(db, local)
        if payload:
            crm.update_invoice(payload)
            fee_removed = True
            local.grand_total = float(payload.get("grandTotal") or 0)
            local.subtotal = float(payload.get("subTotal") or 0)
            local.tax_amount = float(payload.get("taxAmount") or 0)
            local.due_amount = 0.0 if plan.fully_paid else _r2(-plan.variance)
            local.payload = payload
        else:
            warnings.append(
                "Couldn't rebuild this invoice without the 4% fee, so it still "
                "shows the fee in KonaOS. The payment below was still recorded."
            )
    elif local is None:
        warnings.append(
            "This invoice wasn't drafted by this system, so its 4% fee couldn't "
            "be removed. The payment was recorded against it as it stands."
        )

    # 2. The payment. Partial when the check doesn't clear the balance, so the
    #    remainder stays open instead of the invoice reading as settled.
    note = f"Check {plan.check_amount:,.2f}"
    if by:
        note += f" — applied by {by}"
    crm.mark_invoice_paid(
        plan.invoice_id,
        paid_amount=plan.check_amount,
        partial=not plan.fully_paid,
        note=note,
    )

    if local is not None:
        local.status = "paid" if plan.fully_paid else "partially_paid"

    # 3. Our own books, and the trail that answers "who marked this paid".
    entry = (
        db.query(FinancialEntry)
        .filter(FinancialEntry.crm_event_id == plan.event_id)
        .one_or_none()
        if plan.event_id else None
    )
    if entry is not None:
        entry.paid = bool(plan.fully_paid)

    event = db.get(Event, local.event_id) if local is not None else None
    who = by or "a user"
    summary = (
        f"Check for ${plan.check_amount:,.2f} recorded against invoice "
        f"{plan.invoice_number or plan.invoice_id}"
        + (f" — 4% processing fee (${plan.cc_fee_removed:,.2f}) removed first"
           if fee_removed else "")
        + f", by {who}"
    )
    db.add(CrmAuditEntry(
        event_id=event.id if event else None,
        crm_event_id=plan.event_id or (event.crm_event_id if event else ""),
        event_name=event.event_name if event else plan.business_name,
        event_date=event.event_date if event else None,
        action="check_applied",
        summary=summary[:512],
        detail={
            "by": by,
            "invoice_id": plan.invoice_id,
            "invoice_number": plan.invoice_number,
            "check_amount": plan.check_amount,
            "invoice_total_before": plan.invoice_total,
            "cc_fee_removed": plan.cc_fee_removed,
            "amount_due_after_fee": plan.amount_due_after_fee,
            "variance": plan.variance,
            "status": plan.status,
            "fee_removed_in_konaos": fee_removed,
        },
    ))
    db.commit()

    return ApplyResult(
        True, summary,
        invoice_id=plan.invoice_id,
        crm_event_id=plan.event_id,
        warnings=warnings,
        detail={"fully_paid": plan.fully_paid, "variance": plan.variance},
    )


# ── cash ─────────────────────────────────────────────────────────────────────


def _match_row(event: Event) -> dict[str, Any]:
    """One local event in the shape the matcher scores.

    Matching runs against events we have already processed rather than a fresh
    KonaOS query: cash can only be posted where a ledger row exists, so an event
    this system has never seen is unpostable regardless of how well it matches.
    Our stored ``raw`` is the KonaOS payload, so the same fields are there.
    """
    raw = dict(event.raw or {})
    raw["id"] = event.crm_event_id
    raw.setdefault("name", event.event_name)
    if not raw.get("name"):
        raw["name"] = event.event_name
    raw.setdefault("brandName", event.brand)
    return raw


def _cash_candidates(db: Session, on_date: str = "") -> list[Event]:
    query = db.query(Event).filter(Event.crm_event_id != "")
    if on_date:
        return query.filter(Event.event_date == on_date).all()
    cutoff = (date.today() - timedelta(days=CASH_LOOKBACK_DAYS)).isoformat()
    return query.filter(Event.event_date >= cutoff).all()


@dataclass
class CashReview:
    """One "this event took this much" line, matched but not yet posted."""

    entry: CashEntry
    match: MatchResult
    event: Optional[Event] = None
    ledger_found: bool = False
    previous_cash: float = 0.0
    blocked: str = ""

    @property
    def ready(self) -> bool:
        return self.event is not None and self.ledger_found and not self.blocked


def review_cash(
    db: Session,
    entries: list[CashEntry],
    *,
    default_date: str = "",
) -> list[CashReview]:
    """Match each spoken amount to an event, and say what posting it would change.

    An entry that matches nothing, matches two things equally well, or lands on
    an event with no ledger row comes back unresolved with its candidates — the
    reviewer picks, rather than the system guessing which customer's takings
    these were.
    """
    out: list[CashReview] = []
    for entry in entries:
        on_date = entry.date or default_date
        events = _cash_candidates(db, on_date)
        by_crm_id = {e.crm_event_id: e for e in events}
        result = match_event([_match_row(e) for e in events], entry.query, entry.brand)

        review = CashReview(entry=entry, match=result)
        if result.event is not None:
            review.event = by_crm_id.get(result.event_id)
        if entry.amount <= 0:
            review.blocked = "No amount was heard for this one. Type it in."
        if review.event is not None:
            ledger = (
                db.query(FinancialEntry)
                .filter(FinancialEntry.crm_event_id == review.event.crm_event_id)
                .one_or_none()
            )
            review.ledger_found = ledger is not None
            if ledger is None:
                review.blocked = (
                    f"\"{review.event.event_name}\" hasn't been processed yet, so "
                    "there is no ledger row to post cash to. Run it first."
                )
            else:
                review.previous_cash = float(ledger.cash_collected or 0)
        out.append(review)
    return out


def review_cash_for_event(
    db: Session, crm_event_id: str, entry: CashEntry
) -> CashReview:
    """The same review for an event the reviewer picked by hand off the screen."""
    event = (
        db.query(Event)
        .filter(Event.crm_event_id == crm_event_id)
        .order_by(Event.id.desc())
        .first()
    )
    if event is None:
        return CashReview(
            entry=entry,
            match=MatchResult(None, f"No event {crm_event_id} in this system.", []),
            blocked=f"No event {crm_event_id} in this system.",
        )
    ledger = (
        db.query(FinancialEntry)
        .filter(FinancialEntry.crm_event_id == crm_event_id)
        .one_or_none()
    )
    review = CashReview(
        entry=entry,
        match=MatchResult(_match_row(event), "Event chosen by hand."),
        event=event,
        ledger_found=ledger is not None,
        previous_cash=float(ledger.cash_collected or 0) if ledger else 0.0,
    )
    if ledger is None:
        review.blocked = (
            f"\"{event.event_name}\" hasn't been processed yet, so there is no "
            "ledger row to post cash to. Run it first."
        )
    if entry.amount <= 0:
        review.blocked = "No amount was heard for this one. Type it in."
    return review


# ── serialisation for the review screen ──────────────────────────────────────


def _candidate_json(c: Candidate) -> dict[str, Any]:
    return {
        "id": c.id,
        "name": c.name,
        "score": c.score,
        "flags": c.flags,
        "event_date": str(c.event.get("eventDate") or c.event.get("date") or ""),
        "city": str(c.event.get("city") or ""),
    }


def check_review_json(
    review: CheckReview, applied: Optional[dict[str, Any]] = None,
    held_because: str = "",
) -> dict[str, Any]:
    check, match, plan = review.check, review.match, review.plan
    return {
        "kind": "check",
        "ready": review.ready,
        # Set when this settled itself on upload — the screen then reports what
        # happened instead of asking for a confirmation of something already done.
        "applied": applied,
        # Why it did NOT settle itself, in words for the person now looking at it.
        "held_because": held_because,
        "check": {
            "payer_name": check.payer_name,
            "payer_address": check.payer_address,
            "amount": check.amount,
            "check_date": check.check_date,
            "check_number": check.check_number,
            "memo": check.memo,
            "confidence": check.confidence,
            "notes": check.notes,
            "error": check.error,
        },
        "reason": match.reason,
        "needs_choice": match.needs_choice,
        "candidates": [
            {
                "id": c.id,
                "invoice_number": c.number,
                "business_name": c.business,
                "grand_total": c.total,
                "score": c.score,
                "flags": c.flags,
            }
            for c in match.candidates
        ],
        "plan": None if plan is None else {
            "invoice_id": plan.invoice_id,
            "invoice_number": plan.invoice_number,
            "event_id": plan.event_id,
            "business_name": plan.business_name,
            "check_amount": plan.check_amount,
            "invoice_total": plan.invoice_total,
            "cc_fee_removed": plan.cc_fee_removed,
            "amount_due_after_fee": plan.amount_due_after_fee,
            "variance": plan.variance,
            "status": plan.status,
            "fully_paid": plan.fully_paid,
            "warnings": plan.warnings,
        },
    }


def cash_review_json(
    review: CashReview, applied: Optional[dict[str, Any]] = None,
    held_because: str = "",
) -> dict[str, Any]:
    event = review.event
    return {
        "kind": "cash",
        "ready": review.ready,
        "applied": applied,
        "held_because": held_because,
        "heard": {
            "query": review.entry.query,
            "amount": review.entry.amount,
            "brand": review.entry.brand,
            "date": review.entry.date,
        },
        "reason": review.match.reason,
        "needs_choice": review.match.needs_choice,
        "blocked": review.blocked,
        "candidates": [_candidate_json(c) for c in review.match.candidates],
        "event": None if event is None else {
            "crm_event_id": event.crm_event_id,
            "event_name": event.event_name,
            "event_date": event.event_date,
            "brand": event.brand,
            "billing_model": event.billing_model,
        },
        "previous_cash": review.previous_cash,
        "ledger_found": review.ledger_found,
    }
