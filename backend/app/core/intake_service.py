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
                                       match_invoice, memo_dates)
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


def _money(v: Any) -> float:
    """A KonaOS money field as a float. They arrive as numbers or as strings."""
    try:
        return float(str(v).replace(",", "").replace("$", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


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


def fee_free_total(
    db: Session, crm_invoice_id: str, invoice_total: Optional[float] = None
) -> Optional[float]:
    """What this invoice comes to once the 4% processing fee is taken off.

    Computed by re-running the billing engine with ``waive_cc_fee=True``, never
    by subtracting 4% from the grand total. The fee is charged on the PRE-TAX
    subtotal, so peeling it off arithmetically drifts a cent or two — and a cent
    of drift is exactly what turns "paid in full" into "underpaid by $0.01" and
    parks the event on Needs Attention for nothing.

    Two things must be true before a penny is taken off, and neither was checked
    before — which is how a $250 invoice carrying NO fee was presented as owing
    $222.60 "less the 4% card fee" of $27.40, a figure that is not 4% of
    anything on the document:

      1. **A fee has to have been charged.** Most check-paid events never carry
         one: the classifier sees "paid by check" in the notes and the engine
         skips it at drafting time. Removing a fee that was never added invents
         a discount.
      2. **Our recomputation has to reproduce the invoice KonaOS actually
         holds.** If re-running the classification does not land on the same
         grand total, then the invoice was built from something we can no longer
         reconstruct — an edit in KonaOS, a since-changed note, a stale snapshot
         — and the difference between the two figures is NOT a processing fee.
         Presenting it as one is worse than declining: it is a wrong number
         wearing a confident label.

    None whenever we cannot stand behind the figure; the caller then says so and
    the check waits for a person.
    """
    invoice = _local_invoice(db, crm_invoice_id)
    if invoice is None:
        return None
    event = db.get(Event, invoice.event_id)
    if event is None or not event.classification:
        return None

    # As the invoice was actually billed — the baseline everything else is
    # checked against.
    as_billed = billing.calculate_invoice(event.classification)
    # A total stated in the notes overrides the engine, so waiving the fee
    # changes nothing and how much of somebody's typed figure was fee is
    # unknowable.
    if float(as_billed.get("AI_EXTRACTED_INVOICE_AMOUNT") or 0) > 0:
        return None
    billed = _r2(as_billed.get("FINAL_INVOICE_AMOUNT") or 0)
    if billed <= 0:
        return None

    if invoice_total is not None and abs(billed - _r2(invoice_total)) > 0.02:
        return None                                    # guard 2

    if _r2(as_billed.get("CC_FEE") or 0) <= 0:         # guard 1
        return _r2(invoice_total if invoice_total is not None else billed)

    without = billing.calculate_invoice(event.classification, waive_cc_fee=True)
    total = _r2(without.get("FINAL_INVOICE_AMOUNT") or 0)
    return total if total > 0 else None


def fee_free_totals(db: Session, invoices: list[dict[str, Any]]) -> dict[str, float]:
    """The fee-free figure for every open invoice, keyed by CRM invoice id.

    Handed to the matcher so a check written for the fee-free amount — the
    NORMAL case, since the office quotes that figure — scores as an exact match
    instead of looking like an underpayment and matching nothing.
    """
    out: dict[str, float] = {}
    for inv in invoices:
        # Settled invoices included: the matcher scores them so it can say
        # "already paid", and that message quotes the check-payable figure.
        inv_id = str(inv.get("id") or "")
        # The invoice's own total is passed in so the recomputation can be
        # checked against it rather than trusted blind. See fee_free_total.
        total = fee_free_total(db, inv_id, _money(inv.get("grandTotal")))
        if total is not None:
            out[inv_id] = total
    return out


def event_metadata(
    db: Session, invoices: list[dict[str, Any]]
) -> dict[str, dict[str, str]]:
    """Event name and date for every open invoice, keyed by CRM invoice id.

    Ours, not KonaOS's: the invoice grid carries neither reliably, and these are
    what the matcher scores on. An invoice we didn't draft contributes nothing
    and simply matches on its business name and amount as before.
    """
    out: dict[str, dict[str, str]] = {}
    for inv in invoices:
        inv_id = str(inv.get("id") or "")
        local = _local_invoice(db, inv_id)
        event = db.get(Event, local.event_id) if local is not None else None
        if event is None:
            continue
        out[inv_id] = {
            "event_name": event.event_name or "",
            "event_date": event.event_date or "",
        }
    return out


@dataclass
class CheckReview:
    """One check, read and matched, with nothing written yet."""

    check: CheckRead
    match: InvoiceMatch
    plan: Optional[SettlePlan] = None
    # Fee-free total per invoice id, for every candidate shown. Carried on the
    # review so the screen can show what each invoice comes to when paid by
    # check — routinely the figure written on the cheque, and therefore the
    # column that explains why something did or didn't match.
    without_fee: dict[str, float] = field(default_factory=dict)
    # Event name and date per invoice id, from our own records. Carried for the
    # same reason as without_fee: so the candidate rows show the SAME event the
    # plan card does, rather than whatever the KonaOS grid happens to carry.
    event_meta: dict[str, dict[str, str]] = field(default_factory=dict)
    # Several invoices this one cheque settles together, when their totals sum
    # to it exactly. Mutually exclusive with `plan`: either one invoice is being
    # paid or a set is.
    split: list[SettlePlan] = field(default_factory=list)

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
    event_meta = event_metadata(db, invoices)

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
            check_date=check.check_date,
            event_meta=event_meta,
            memo=check.memo,
            invoice_number=check.invoice_number,
        )

    if match.invoice is None:
        # A cheque covering several invoices — arithmetic, not inference. Each
        # part is planned on its own so the fee comes off each one properly and
        # every invoice ends up settled by the engine, not by division here.
        split = [
            _planned(db, c.invoice, without_fee.get(c.id), c.total)
            for c in match.combination
        ]
        return CheckReview(check=check, match=match, without_fee=without_fee,
                           event_meta=event_meta, split=split)

    plan = build_settle_plan(
        match.invoice,
        check.amount,
        fee_free_total=without_fee.get(match.invoice_id),
    )
    plan.payment_method = "CHECK"
    _name_the_event(db, plan, match.invoice)
    return CheckReview(check=check, match=match, plan=plan,
                       without_fee=without_fee, event_meta=event_meta)


def _planned(
    db: Session, invoice: dict[str, Any], fee_free: Optional[float],
    fallback: float,
) -> SettlePlan:
    """One part of a split cheque: this invoice, paid in full by cheque."""
    plan = build_settle_plan(
        invoice, fee_free if fee_free is not None else fallback,
        fee_free_total=fee_free,
    )
    plan.payment_method = "CHECK"
    _name_the_event(db, plan, invoice)
    return plan


def _name_the_event(
    db: Session, plan: SettlePlan, invoice: dict[str, Any]
) -> None:
    """Fill in which event this check settles, for the screen and the audit line.

    Our own records first: the local invoice knows its event, and that event has
    the name and date the rest of the dashboard shows, so the same event reads
    the same everywhere. The KonaOS payload is the fallback for an invoice we
    didn't draft.
    """
    local = _local_invoice(db, plan.invoice_id)
    event = db.get(Event, local.event_id) if local is not None else None
    if event is not None:
        plan.event_name = event.event_name or ""
        plan.event_date = event.event_date or ""
        if not plan.event_id:
            plan.event_id = event.crm_event_id or ""
        return
    plan.event_name = str(_first(invoice, "eventName", "eventTitle", "title"))
    plan.event_date = _konaos_date(_first(invoice, "eventDate", "eventStartDate"))


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
    # Name the event, not just the invoice. "Invoice 00084 is paid" is only
    # checkable by someone willing to go and look it up; "Featherbed Lane,
    # 2026-05-08" is recognised on sight by whoever booked it.
    against = plan.invoice_number or plan.invoice_id
    if plan.event_name:
        against += f" ({plan.event_name}"
        against += f", {plan.event_date})" if plan.event_date else ")"
    summary = (
        f"Check for ${plan.check_amount:,.2f} recorded against invoice {against}"
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
    # Stamp OUR date on, unconditionally. KonaOS dates an event with
    # `startDateTime` in epoch milliseconds; nothing in the payload is called
    # `eventDate`, so reading that key returned "" for every candidate and the
    # screen fell back to printing the town under a heading that said DATE. Our
    # own column is already the ISO date the rest of the dashboard displays.
    raw["eventDate"] = event.event_date or ""
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

        # A stated date that matches no event at all is far more likely to be a
        # misheard or mis-resolved date than a real claim that the day was empty
        # — "29th July" with the wrong year lands on a day that has never had an
        # event on it. Dead-ending there tells the user their event doesn't
        # exist, which is both wrong and unactionable. Widen to the recent window
        # instead and let the name do the work the date failed to do.
        widened = False
        if on_date and not events:
            events = _cash_candidates(db, "")
            widened = bool(events)

        by_crm_id = {e.crm_event_id: e for e in events}
        result = match_event([_match_row(e) for e in events], entry.query, entry.brand)
        if widened:
            result.reason = (
                f"Nothing on {on_date}, so this looked across the last "
                f"{CASH_LOOKBACK_DAYS} days instead. {result.reason}"
            )

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


def _konaos_date(value: Any) -> str:
    """A KonaOS date field as YYYY-MM-DD, or "".

    Dates arrive as epoch milliseconds, but not always — the grid is not
    documented and different endpoints have handed us strings before. Accept
    both rather than showing a reviewer "1746316800000" where a date should be.
    """
    if value in (None, "", 0):
        return ""
    text = str(value).strip()
    if text.isdigit():
        try:
            from datetime import datetime, timezone

            return datetime.fromtimestamp(
                int(text) / 1000, tz=timezone.utc
            ).date().isoformat()
        except (ValueError, OSError, OverflowError):
            return ""
    # Already a date, possibly with a time on the end. Anything that isn't
    # shaped like one is dropped rather than displayed — a stray field printed
    # in a column headed "date" is worse than a blank, because it reads as data.
    head = text[:10]
    try:
        from datetime import date as _date

        _date.fromisoformat(head)
    except ValueError:
        return ""
    return head


def _first(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return ""


def _reconcile(ours: str, konaos: str) -> tuple[str, str, str]:
    """One field held in two places. Returns (value, source, the other value).

    Both sources are consulted and the disagreement is reported rather than
    resolved silently. Which one wins matters less than the fact that they
    differ: our copy is a snapshot taken when the event was processed, and
    KonaOS is edited afterwards — a name or date that has since changed there
    is exactly the kind of drift nobody notices until an invoice goes out wrong.

    "ours only" is worth seeing too. It means KonaOS's grid is missing a field
    we hold, which is a gap in the CRM rather than in this system.
    """
    ours, konaos = (ours or "").strip(), (konaos or "").strip()
    if ours and konaos:
        same = ours.casefold() == konaos.casefold()
        return ours, ("both agree" if same else "differs from KonaOS"), konaos
    if ours:
        return ours, "ours only — not in KonaOS", ""
    if konaos:
        return konaos, "KonaOS only", ""
    return "", "", ""


def _invoice_candidate_json(
    c: InvoiceCandidate,
    without_fee: Optional[float] = None,
    meta: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """One invoice the check might be paying, with everything needed to choose.

    A reviewer deciding between two invoices for the same business needs the
    event and the date to tell them apart — the invoice number alone means
    nothing to anybody, and the business name is identical by definition when
    the choice is hard. The fee-free figure is here because that is what a check
    is normally written for, so it is often the column that matches.
    """
    inv = c.invoice
    meta = meta or {}
    # Both sources, reconciled and reported — see _reconcile. Ours is preferred
    # for display because it is what the matcher scored and what the rest of the
    # dashboard shows, but where KonaOS disagrees or is missing it, the screen
    # says so instead of quietly picking one.
    name, name_source, name_konaos = _reconcile(
        meta.get("event_name", ""),
        str(_first(inv, "eventName", "eventTitle", "title", "name")),
    )
    edate, date_source, date_konaos = _reconcile(
        meta.get("event_date", ""),
        _konaos_date(_first(inv, "eventDate", "eventStartDate")),
    )
    return {
        "id": c.id,
        "invoice_number": c.number,
        "business_name": c.business,
        "event_name": name,
        "event_name_source": name_source,
        "event_name_konaos": name_konaos,
        "event_date": edate,
        "event_date_source": date_source,
        "event_date_konaos": date_konaos,
        "invoice_date": _konaos_date(_first(inv, "invoiceDate", "createdDate")),
        "status": str(_first(inv, "invoiceStatus", "status")),
        "grand_total": c.total,
        # What the client owes if they pay by check. Often the figure on the
        # cheque itself, and therefore the one that explains a "no match".
        "total_without_fee": without_fee,
        "score": c.score,
        "flags": c.flags,
    }


def _candidate_json(c: Candidate) -> dict[str, Any]:
    return {
        "id": c.id,
        "name": c.name,
        "score": c.score,
        "flags": c.flags,
        # startDateTime is KonaOS's own field and is epoch ms; _konaos_date
        # handles that, and returns "" for anything not shaped like a date
        # rather than passing a raw number through to a date column.
        "event_date": _konaos_date(
            _first(c.event, "eventDate", "date", "startDateTime")
        ),
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
            "invoice_number": check.invoice_number,
            "memo": check.memo,
            "confidence": check.confidence,
            "notes": check.notes,
            "error": check.error,
            # Event dates the memo names ("Kona Ice on 7/9 and 7/21"). Shown so
            # a reviewer can see the system read them — they outrank the
            # cheque's own date when scoring, and that is worth being visible.
            "memo_dates": memo_dates(check.memo, check.check_date),
        },
        "reason": match.reason,
        "needs_choice": match.needs_choice,
        # An invoice that fits but is already marked paid — shown so somebody
        # holding the cheque can see it has been dealt with, rather than being
        # told nothing matches and going looking for it by hand.
        "already_paid": None if match.settled is None else _invoice_candidate_json(
            match.settled, review.without_fee.get(match.settled.id),
            review.event_meta.get(match.settled.id),
        ),
        "candidates": [
            _invoice_candidate_json(c, review.without_fee.get(c.id),
                                    review.event_meta.get(c.id))
            for c in match.candidates
        ],
        # Each invoice a split cheque settles, planned in full.
        "split": [
            {
                "invoice_id": p.invoice_id,
                "invoice_number": p.invoice_number,
                "event_name": p.event_name,
                "event_date": p.event_date,
                "business_name": p.business_name,
                "invoice_total": p.invoice_total,
                "cc_fee_removed": p.cc_fee_removed,
                "amount_due_after_fee": p.amount_due_after_fee,
            }
            for p in review.split
        ],
        "split_total": _r2(sum(p.amount_due_after_fee for p in review.split)),
        "plan": None if plan is None else {
            "invoice_id": plan.invoice_id,
            "invoice_number": plan.invoice_number,
            "event_id": plan.event_id,
            "event_name": plan.event_name,
            "event_date": plan.event_date,
            "business_name": plan.business_name,
            "check_amount": plan.check_amount,
            "invoice_total": plan.invoice_total,
            "cc_fee_removed": plan.cc_fee_removed,
            "amount_due_after_fee": plan.amount_due_after_fee,
            "variance": plan.variance,
            "status": plan.status,
            "fully_paid": plan.fully_paid,
            "warnings": plan.warnings,
            "notes": plan.notes,
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
