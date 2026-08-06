"""Working out what a received check settles, before anything is written.

Given a check we have read (payer, amount) and the open invoices KonaOS holds,
this decides which invoice it pays and what should change. It writes nothing —
the caller shows the plan, a person agrees, and only then does it happen.

Two departures from the n8n workflow this replaces, both deliberate:

**Matched on WHO and WHEN; the amount corroborates.** The workflow searched
events by the CHECK's date, which only works because checks usually arrive near
the event — a school paying three weeks later misses the window entirely. We
score the payer against the invoice's business AND event names, and the dates
the cheque carries against the event's, with the amount as supporting evidence
rather than the only way in. It was the only way in once, and it is the field
least often in agreement: a cheque covers two events at once, or arrives before
the invoice is drafted, or the client rounds.

Dates come from the memo first and the cheque's own date second. "Kona Ice on
7/9 and 7/21" states which events are being paid for; the date in the corner
only says when somebody sat down with the chequebook.

None of this decides whether a match may be WRITTEN unattended — that still
requires the figures to agree to the cent. See
``intake_service.auto_applicable_check``. Matching is generous; writing is not.

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

# WHO and WHEN identify the invoice; the amount corroborates it.
#
# The amount used to be the only route to a confident match, and in practice it
# is the field least often in agreement: a check pays several invoices at once,
# or arrives before the invoice is drafted, or the client rounds. Meanwhile the
# name on the check and the name on the invoice usually agree exactly, and the
# date the check was written sits close to the event it pays for.
#
# So a full name match now reaches the floor on its own, and so does an exact
# amount — either is sufficient, together they are decisive. What the amount
# still governs is whether the match may APPLY ITSELF: see
# intake_service.auto_applicable_check, which requires the figures to agree to
# the cent before anything is written without a person looking.
_EXACT_AMOUNT_POINTS = 50
_NEAR_AMOUNT_POINTS = 15
_NAME_ALL_TOKENS = 50
_NAME_MOST_TOKENS = 30
_NAME_SOME_TOKENS = 10

# The check's own date against the event's. Checks are written around the event
# — usually after it, sometimes before as a deposit — so proximity is real
# evidence, and it is what separates two invoices to the same customer.
_DATE_CLOSE_DAYS = 15
_DATE_NEAR_DAYS = 45
_DATE_WIDE_DAYS = 120
_DATE_CLOSE_POINTS = 25
_DATE_NEAR_POINTS = 20
_DATE_WIDE_POINTS = 10
# A date written in the MEMO — "Kona Ice on 7/9 and 7/21" — says which event is
# being paid for, where the cheque's own date only says when somebody sat down
# with the chequebook. Still short of the floor on its own, so it needs the name
# or the amount to corroborate; but it must beat mere proximity by MORE than
# AMBIGUITY_MARGIN, or a cheque that STATES which event it pays comes back as
# ambiguous with one that merely happens to fall nearby.
_MEMO_DATE_POINTS = 40

# An invoice number printed on the cheque or its remittance slip is not a
# signal, it is a KEY. It clears the floor on its own and outruns the ambiguity
# margin against anything scored, so an exact hit ends the argument — which is
# the whole point of asking for it.
_INVOICE_NUMBER_POINTS = 100

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


def _one_name_points(payer: str, candidate: str) -> int:
    p_tokens = _significant(_tokens(payer))
    c_tokens = _tokens(candidate)
    if not p_tokens or not c_tokens:
        return 0
    hits = sum(1 for t in p_tokens if any(t == ct or t in ct for ct in c_tokens))
    if hits == len(p_tokens):
        return _NAME_ALL_TOKENS
    if hits / len(p_tokens) > 0.5:
        return _NAME_MOST_TOKENS
    if hits:
        return _NAME_SOME_TOKENS
    return 0


def _name_points(
    payer: str, business: str, event_name: str = ""
) -> tuple[int, str]:
    """How much the check's payer looks like this invoice's names.

    Scored against the business name AND the event name, best of the two. They
    diverge more often than not — an invoice's business is "Baltimore County
    Public Schools" while the event is "Featherbed Lane Elementary", and the
    cheque is printed with whichever the payer thinks of as themselves.
    Requiring the business name alone threw away the half of the checks that
    carry the event's name instead.
    """
    business_points = _one_name_points(payer, business)
    event_points = _one_name_points(payer, event_name) if event_name else 0
    points = max(business_points, event_points)
    if not points:
        return 0, "name+0"
    which = "event name" if event_points > business_points else "business name"
    detail = {
        _NAME_ALL_TOKENS: "all tokens",
        _NAME_MOST_TOKENS: "most tokens",
        _NAME_SOME_TOKENS: "some tokens",
    }[points]
    return points, f"name+{points} {detail} ({which})"


_MEMO_MD = re.compile(r"\b(\d{1,2})\s*/\s*(\d{1,2})(?:\s*/\s*(\d{2,4}))?\b")
_MONTHS = ("january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december")
_MEMO_MONTH_DAY = re.compile(
    r"\b(" + "|".join(_MONTHS) + r")\w*\s+(\d{1,2})\b", re.I)


def memo_dates(memo: str, check_date: str = "") -> list[str]:
    """Event dates named in the memo line, as ISO strings.

    "Kona Ice on 7/9 and 7/21" is the payer telling us exactly which events this
    cheque covers — better evidence than any inference from the date it was
    written, which is merely when somebody sat down with the chequebook.

    A bare "7/9" carries no year, so it takes the check's. Where that would put
    the date in the future relative to the cheque — "12/20" on a cheque written
    in January — it steps back a year, because a memo describes events that have
    happened.
    """
    text = (memo or "").strip()
    if not text:
        return []
    from datetime import date as _date

    try:
        base = _date.fromisoformat(check_date[:10]) if check_date else None
    except ValueError:
        base = None
    year = base.year if base else _date.today().year

    found: list[str] = []
    pairs = [(int(m), int(d), yy) for m, d, yy in _MEMO_MD.findall(text)]
    pairs += [(_MONTHS.index(name.lower()[:3] and
                            next(mn for mn in _MONTHS
                                 if mn.startswith(name.lower()[:3]))) + 1,
               int(day), None)
              for name, day in _MEMO_MONTH_DAY.findall(text)]

    for month, day, raw_year in pairs:
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        y = year
        if raw_year:
            y = int(raw_year)
            if y < 100:
                y += 2000
        try:
            candidate = _date(y, month, day)
        except ValueError:
            continue
        # A memo describes what has already happened.
        if base and candidate > base and not raw_year:
            try:
                candidate = _date(y - 1, month, day)
            except ValueError:
                continue
        iso = candidate.isoformat()
        if iso not in found:
            found.append(iso)
    return found


def _normalise_invoice_number(value: str) -> str:
    """Comparable form of an invoice number.

    Case and punctuation vary between what is printed on a remittance slip and
    what KonaOS holds ("00084" / "#00084" / "K530X-9179333"), and leading zeros
    are dropped by anything that has been near a spreadsheet — so compare on the
    digits and letters alone, with a leading-zero-stripped variant as well.
    """
    return re.sub(r"[^A-Za-z0-9]", "", value or "").upper()


def _same_invoice_number(printed: str, invoice: dict[str, Any]) -> bool:
    want = _normalise_invoice_number(printed)
    if not want:
        return False
    for key in ("invoiceNumber", "invoiceNo", "number"):
        have = _normalise_invoice_number(str(invoice.get(key) or ""))
        if not have:
            continue
        if have == want or have.lstrip("0") == want.lstrip("0"):
            return True
    return False


def _date_points(
    check_date: str, event_date: str, named: Optional[list[str]] = None
) -> tuple[int, str]:
    """How well the dates line up.

    A date the MEMO names is worth more than the cheque's own date, because it
    states which event is being paid for rather than merely when the payment was
    written. Deliberately not enough on its own to clear the floor — a memo date
    with a payer matching nothing is still a guess.

    Absent any date this contributes nothing rather than penalising: plenty of
    cheques are undated, and a missing field is not evidence against a match.
    """
    if event_date and named and event_date[:10] in named:
        return _MEMO_DATE_POINTS, f"date+{_MEMO_DATE_POINTS} memo names {event_date[:10]}"
    if not check_date or not event_date:
        return 0, "date+0"
    try:
        from datetime import date as _date

        gap = abs((_date.fromisoformat(check_date[:10])
                   - _date.fromisoformat(event_date[:10])).days)
    except ValueError:
        return 0, "date+0"
    # A fortnight either side is the ordinary rhythm: the event happens, the
    # invoice goes out, the cheque is written. Brett's own framing.
    if gap <= _DATE_CLOSE_DAYS:
        return _DATE_CLOSE_POINTS, f"date+{_DATE_CLOSE_POINTS} within {gap}d"
    if gap <= _DATE_NEAR_DAYS:
        return _DATE_NEAR_POINTS, f"date+{_DATE_NEAR_POINTS} within {gap}d"
    if gap <= _DATE_WIDE_DAYS:
        return _DATE_WIDE_POINTS, f"date+{_DATE_WIDE_POINTS} within {gap}d"
    return 0, f"date+0 ({gap}d apart)"


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
    # An invoice that matches well but is ALREADY marked paid. Settled invoices
    # used to be filtered out before scoring, so a cheque for one reported "no
    # unpaid invoice matches" — which is true, and useless: the answer the
    # person needs is "you have already recorded this", not "no idea".
    settled: Optional[InvoiceCandidate] = None
    # Several invoices whose totals sum to this cheque. One cheque covering two
    # events cannot be resolved by scoring invoices one at a time — both halves
    # match equally well, so it ties and asks, and the true answer is "both".
    combination: list[InvoiceCandidate] = field(default_factory=list)

    @property
    def invoice_id(self) -> str:
        return str((self.invoice or {}).get("id") or "")


def match_invoice(
    invoices: list[dict[str, Any]],
    payer_name: str,
    amount: float,
    *,
    without_fee_amounts: Optional[dict[str, float]] = None,
    check_date: str = "",
    event_meta: Optional[dict[str, dict[str, str]]] = None,
    memo: str = "",
    invoice_number: str = "",
) -> InvoiceMatch:
    """Which open invoice this check pays.

    ``without_fee_amounts`` maps invoice id to what that invoice would total with
    the 4% processing fee removed. A check written against the fee-free figure is
    the NORMAL case — the office quotes it that way — so it has to score as an
    exact amount match, otherwise every correctly-written check looks like an
    underpayment and matches nothing.

    ``event_meta`` maps invoice id to ``{"event_name", "event_date"}`` from our
    own records. The invoice grid does not reliably carry either, and they are
    what a person matches on: the name printed on the cheque against the job it
    paid for, and the date it was written against the date that job ran.
    """
    without_fee = without_fee_amounts or {}
    meta = event_meta or {}
    named_dates = memo_dates(memo, check_date)
    candidates: list[InvoiceCandidate] = []

    for inv in invoices:
        # Settled invoices are scored too, not skipped. See InvoiceMatch.settled:
        # "you have already recorded this" is the answer a person needs, and it
        # is unreachable if the only matching invoice was filtered out first.
        inv_id = str(inv.get("id") or "")
        inv_meta = meta.get(inv_id, {})
        flags: list[str] = []
        score = 0

        # THE KEY, when the cheque carries one. Everything below is inference;
        # this is the payer telling us the answer.
        if invoice_number and _same_invoice_number(invoice_number, inv):
            score += _INVOICE_NUMBER_POINTS
            flags.append(f"invoice#+{_INVOICE_NUMBER_POINTS} printed on the cheque")

        # WHO. Business name or event name, whichever the cheque was written to.
        name_pts, name_flag = _name_points(
            payer_name,
            str(inv.get("businessName") or ""),
            inv_meta.get("event_name", ""),
        )
        score += name_pts
        flags.append(name_flag)

        # WHEN. The check's date against the event's.
        date_pts, date_flag = _date_points(
            check_date, inv_meta.get("event_date", ""), named_dates)
        score += date_pts
        flags.append(date_flag)

        # HOW MUCH. Corroboration, no longer the only way in — but still the
        # thing that decides whether this may apply without a person looking.
        total = _num(inv.get("grandTotal"))
        fee_free = without_fee.get(inv_id)
        if total > 0 and abs(total - amount) <= _TOL:
            score += _EXACT_AMOUNT_POINTS
            flags.append(f"amount+{_EXACT_AMOUNT_POINTS} exact")
        elif fee_free is not None and abs(fee_free - amount) <= _TOL:
            score += _EXACT_AMOUNT_POINTS
            flags.append(f"amount+{_EXACT_AMOUNT_POINTS} exact less the 4% fee")
        elif total > 0 and abs(total - amount) <= max(1.0, total * 0.05):
            score += _NEAR_AMOUNT_POINTS
            flags.append(f"amount+{_NEAR_AMOUNT_POINTS} within 5%")
        else:
            flags.append("amount+0")

        candidates.append(InvoiceCandidate(invoice=inv, score=score, flags=flags))

    candidates.sort(key=lambda c: c.score, reverse=True)
    open_ones = [c for c in candidates if not is_settled(c.invoice)]
    settled_ones = [c for c in candidates if is_settled(c.invoice)]

    # The best already-paid invoice, if it matches well enough to be worth
    # naming. Reported alongside every outcome below: even when an OPEN invoice
    # matches, knowing a settled one also fits is what stops the same cheque
    # being recorded twice against two different invoices.
    paid_hit = (settled_ones[0]
                if settled_ones and settled_ones[0].score >= MIN_CONFIDENT_SCORE
                else None)

    # Only invoices that already look like this payer's are eligible to be
    # summed. Without that the search would assemble a total out of unrelated
    # customers who happen to add up, which is arithmetic in service of nonsense.
    plausible = [c for c in open_ones if "name+0" not in c.flags]
    combo = find_combination(plausible, amount, without_fee) or []

    def _already_paid_note(c: InvoiceCandidate) -> str:
        return (f"Invoice {c.number or c.id} for {c.business} is already marked "
                f"PAID in KonaOS (${c.total:,.2f}). This cheque looks like one "
                "that has already been recorded.")

    if not open_ones:
        return InvoiceMatch(
            None,
            _already_paid_note(paid_hit) if paid_hit
            else "There are no unpaid invoices to match this check against.",
            candidates[:5],
            needs_choice=bool(paid_hit),
            settled=paid_hit,
            combination=combo,
        )

    best = open_ones[0]

    # An already-paid invoice that fits BETTER than anything still open ends it.
    # Offering the open one anyway is how the Arbutus cheque — paid weeks ago,
    # its memo naming its own event — came to be offered against a different
    # customer's invoice that merely shared its total. Recording that would take
    # one payment and post it to two places, one of them wrong.
    if paid_hit and paid_hit.score >= best.score:
        return InvoiceMatch(
            None,
            _already_paid_note(paid_hit),
            candidates[:5],
            needs_choice=False,      # nothing to choose: there is nothing to do
            settled=paid_hit,
            combination=combo,
        )

    if best.score < MIN_CONFIDENT_SCORE:
        return InvoiceMatch(
            None,
            _already_paid_note(paid_hit) if paid_hit else
            f"No unpaid invoice confidently matches \"{payer_name}\" for "
            f"${amount:,.2f}. Pick one below, or check the amount was read right.",
            candidates[:5],
            needs_choice=True,
            settled=paid_hit,
            combination=combo,
        )

    # A cheque that EXACTLY covers several invoices, against a single invoice
    # that matched without the amount agreeing at all.
    #
    # This is the "Kona Ice on 7/9 and 7/21" cheque: $530 for two $265 events.
    # One invoice scored 75 on the payer's name plus mere calendar proximity —
    # its event fell 10 days after the cheque was written — while contributing
    # `amount+0`, because $530 is not $265. That cleared the confidence floor,
    # and a confident single match used to end the search, so the split was
    # computed and thrown away and the missing half was reported as an
    # OVERPAYMENT of exactly the amount of the invoice nobody looked at.
    #
    # The rule: a unique set of this payer's invoices that sums to the cheque to
    # the penny beats a single invoice the amount does not support. Arithmetic
    # is evidence; being written in the same fortnight is a coincidence that
    # happens to most invoices of a regular customer.
    #
    # Narrow on purpose. It needs `amount+0` — the amount agreeing even loosely
    # (exact, fee-free, or within 5%) leaves the single match standing, so a
    # correctly-matched cheque is never talked out of itself by a coincidental
    # sum. And find_combination already refuses to return anything when two
    # different sets reach the total.
    if len(combo) > 1 and "amount+0" in best.flags:
        parts = ", ".join(c.number or c.id for c in combo)
        return InvoiceMatch(
            None,
            f"This cheque covers {len(combo)} invoices that together come to "
            f"${amount:,.2f} exactly — {parts}. (Invoice "
            f"{best.number or best.id} matched on the payer's name and the "
            f"date, but is ${best.total:,.2f}, which this cheque is not for.)",
            candidates[:5],
            settled=paid_hit,
            combination=combo,
        )

    runner_up = open_ones[1] if len(open_ones) > 1 else None
    if runner_up and best.score - runner_up.score < AMBIGUITY_MARGIN:
        return InvoiceMatch(
            None,
            f"Invoices {best.number or best.id} and "
            f"{runner_up.number or runner_up.id} match this check about equally "
            "well. Choose which one it pays.",
            candidates[:5],
            needs_choice=True,
            settled=paid_hit,
            combination=combo,
        )

    return InvoiceMatch(
        best.invoice,
        f"Matched invoice {best.number or best.id} for {best.business} "
        f"({', '.join(best.flags)}).",
        candidates[:5],
        settled=paid_hit,
        combination=combo,
    )


# One cheque, several invoices. Bounded deliberately: subset-sum is exponential,
# and beyond a handful of invoices a "unique combination" stops being evidence
# and becomes numerology — with enough figures, something always adds up.
_COMBO_MAX_INVOICES = 12
_COMBO_MAX_PARTS = 4


def find_combination(
    candidates: list[InvoiceCandidate],
    amount: float,
    without_fee_amounts: Optional[dict[str, float]] = None,
) -> Optional[list[InvoiceCandidate]]:
    """The set of invoices that sums to this cheque, when exactly one does.

    A cheque covering several events is normal here — "Kona Ice on 7/9 and
    7/21", $300 + $230 = $530 — and scoring one invoice at a time can never
    resolve it: both halves match equally, so it ties and asks, and the answer
    to "which one does it pay" is "both".

    This is arithmetic rather than inference, which makes it the strongest
    signal available after a printed invoice number. Two guards keep it honest:

      * every invoice in the set must already look plausible on its own (the
        caller passes only name-matched candidates), so the sum is not assembled
        out of unrelated customers that happen to add up; and
      * the combination must be UNIQUE. If two different sets both reach the
        total, nothing is returned — a coincidence is not a discovery.

    Fee-free totals are tried alongside grand totals for each invoice, because a
    cheque covering three events has had the 4% taken off all three.
    """
    without_fee = without_fee_amounts or {}
    pool = candidates[:_COMBO_MAX_INVOICES]
    if not pool or amount <= 0:
        return None

    hits: list[list[InvoiceCandidate]] = []

    def walk(index: int, chosen: list[InvoiceCandidate], running: float) -> None:
        if len(hits) > 1:
            return                                   # already ambiguous
        if chosen and abs(running - amount) <= _TOL:
            hits.append(list(chosen))
            return
        if (index >= len(pool) or len(chosen) >= _COMBO_MAX_PARTS
                or running > amount + _TOL):
            return
        for i in range(index, len(pool)):
            c = pool[i]
            for value in {c.total, without_fee.get(c.id, c.total)}:
                if value <= 0:
                    continue
                chosen.append(c)
                walk(i + 1, chosen, _r2(running + value))
                chosen.pop()

    walk(0, [], 0.0)
    # A single invoice matching on its own is not a "combination" — that is the
    # ordinary path, and routing it through here would bypass the fee handling
    # and the variance reporting that the single-invoice plan does properly.
    if len(hits) == 1 and len(hits[0]) > 1:
        return hits[0]
    return None


@dataclass
class SettlePlan:
    """Exactly what applying this check will change. Nothing has happened yet."""

    invoice_id: str = ""
    invoice_number: str = ""
    event_id: str = ""
    business_name: str = ""
    # Which event this settles. An invoice number identifies the document; the
    # event and its date are what a person recognises — and the only way to tell
    # this is the right one when a customer has several invoices open. Filled by
    # the caller from our own records, which know the event; the invoice payload
    # does not reliably carry it.
    event_name: str = ""
    event_date: str = ""

    check_amount: float = 0.0
    invoice_total: float = 0.0          # as KonaOS holds it now, fee included
    cc_fee_removed: float = 0.0         # the 4% that comes off for a check
    amount_due_after_fee: float = 0.0   # what the client actually owes

    variance: float = 0.0               # check − amount_due_after_fee
    status: str = "exact"               # exact | underpaid | overpaid
    fully_paid: bool = True

    payment_method: str = "CHECK"
    warnings: list[str] = field(default_factory=list)
    # Things worth saying that are NOT reasons to hesitate. Kept apart from
    # warnings because auto-apply refuses on any warning, and "this invoice
    # never carried a card fee" is the ordinary state of a check-paid event —
    # treating it as a caveat would stop exactly the cleanest cheques from
    # settling themselves.
    notes: list[str] = field(default_factory=list)

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
    # Whether the fee position is KNOWN, as against assumed. When it isn't, the
    # figures below are the invoice as it stands and nothing may be claimed
    # about a fee either way.
    fee_is_known = fee_free_total is not None
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

    # Only claimable when the fee position is known. Saying "this carried no
    # fee" directly beneath "couldn't work out whether it carried one" is a
    # contradiction, and the reader has to decide which half to believe.
    if plan.cc_fee_removed <= 0 and fee_is_known:
        plan.notes.append(
            "This invoice carried no 4% processing fee, so there was nothing to "
            "take off — the client owes what the invoice says."
        )
    if not plan.event_id:
        plan.warnings.append(
            "This invoice isn't linked to an event in KonaOS, so the event's own "
            "payment fields can't be updated."
        )
    return plan
