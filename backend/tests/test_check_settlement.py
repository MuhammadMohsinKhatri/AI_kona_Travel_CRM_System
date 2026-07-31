"""A received check: which invoice it pays, and what it settles.

The rule from the office, and the reason the figures never tie up otherwise: a
check doesn't go through card processing, so the 4% processing fee comes off and
the client owes the smaller figure. A check written for that smaller figure is
the NORMAL case — so it has to read as paid in full, not as an underpayment.
"""
import os

os.environ.setdefault("CRM_PROVIDER", "mock")

from app.core.check_settlement import (build_settle_plan, is_settled,
                                       match_invoice)

# ThriftBooks as it actually stands: $124 servings, $7.44 tax, $4.96 fee.
THRIFTBOOKS = {
    "id": "a89b7adbdd1b4790bf3adeb67a195fe4",
    "invoiceNumber": "K530X9179333",
    "eventId": "be6ba87e37b648f99aee4f7d620e64a2",
    "businessName": "ThriftBooks",
    "grandTotal": 136.40,
    "invoiceStatus": "draft",
}
FEE_FREE_THRIFTBOOKS = 131.44          # 124.00 + 7.44 tax, no 4% fee

JONES = {
    "id": "inv-jones", "invoiceNumber": "00849", "eventId": "ev-jones",
    "businessName": "JONES ELEMENTARY SCHOOL PTA", "grandTotal": 408.10,
    "invoiceStatus": "draft",
}
PAID = {
    "id": "inv-paid", "invoiceNumber": "00700", "eventId": "ev-paid",
    "businessName": "ThriftBooks", "grandTotal": 136.40, "invoiceStatus": "paid",
}


# ── which invoice does this check pay ────────────────────────────────────────

def test_matches_on_payer_and_exact_amount():
    r = match_invoice([THRIFTBOOKS, JONES], "JONES ELEMENTARY SCHOOL PTA", 408.10)
    assert r.invoice_id == "inv-jones"


def test_a_check_written_without_the_4_percent_fee_still_matches():
    """The office quotes the fee-free figure, so this is the common case. If it
    didn't score as exact, every correctly-written check would match nothing."""
    r = match_invoice(
        [THRIFTBOOKS, JONES], "ThriftBooks", 131.44,
        without_fee_amounts={THRIFTBOOKS["id"]: FEE_FREE_THRIFTBOOKS},
    )
    assert r.invoice_id == THRIFTBOOKS["id"]
    assert any("less the 4% fee" in f for f in r.candidates[0].flags)


def test_already_paid_invoices_are_never_re_settled_and_say_so():
    """Still never re-settled — but it now NAMES the paid invoice instead of
    reporting that nothing matches. Brett, holding the cheque: "this one is
    paid, it should show that it's already paid." Being told nothing matches
    sends somebody hunting for an invoice that was dealt with weeks ago."""
    r = match_invoice([PAID], "ThriftBooks", 136.40)
    assert r.invoice is None                     # never re-settled
    assert r.settled is not None                 # but identified
    assert r.settled.number == "00700"
    assert "already marked paid" in r.reason.lower()


def test_is_settled_recognises_the_terminal_states():
    for status in ("paid", "Paid", "void", "cancelled", "refunded"):
        assert is_settled({"invoiceStatus": status}), status
    assert is_settled({"invoiceStatus": "draft", "manuallyMarkedAsPaid": True})
    assert not is_settled({"invoiceStatus": "draft"})


def test_a_full_name_match_identifies_the_invoice_on_its_own():
    """Brett's rule: match on who and when, not on how much.

    The amount is the field least often in agreement — a cheque covers several
    invoices, or arrives before one is drafted, or the client rounds — while the
    name on the cheque and the name on the invoice usually agree exactly. What
    stops a name-only match from being dangerous is not refusing to make it, but
    refusing to APPLY it unattended: see the auto-apply tests in
    test_intake_service.
    """
    r = match_invoice([JONES], "JONES ELEMENTARY SCHOOL PTA", 9999.00)
    assert r.invoice_id == "inv-jones"
    assert any("name+50" in f for f in r.candidates[0].flags)


def test_the_check_date_separates_two_invoices_to_the_same_customer():
    """Where the name ties, the date is what a person would use — and it is on
    the cheque, printed or handwritten, on nearly every one."""
    june = {**JONES, "id": "inv-june", "invoiceNumber": "00860"}
    r = match_invoice(
        [JONES, june], "JONES ELEMENTARY SCHOOL PTA", 9999.00,
        check_date="2026-07-30",
        event_meta={
            "inv-jones": {"event_name": "Jones Field Day", "event_date": "2026-07-28"},
            "inv-june": {"event_name": "Jones Spring Fair", "event_date": "2026-02-01"},
        },
    )
    assert r.invoice_id == "inv-jones"
    assert any("date+25" in f for f in r.candidates[0].flags)


def test_a_cheque_written_to_the_event_rather_than_the_business_still_matches():
    """"Featherbed Lane Elementary" on the cheque, "Baltimore County Public
    Schools" on the invoice. Scoring the business name alone threw these away."""
    inv = {**JONES, "businessName": "Baltimore County Public Schools"}
    r = match_invoice(
        [inv], "FEATHERBED LANE ELEMENTARY SCHOOL", 9999.00,
        event_meta={"inv-jones": {"event_name": "Featherbed Lane Elementary",
                                  "event_date": ""}},
    )
    assert r.invoice_id == "inv-jones"
    assert any("event name" in f for f in r.candidates[0].flags)


def test_two_invoices_that_match_equally_ask_which():
    twin = {**JONES, "id": "inv-twin", "invoiceNumber": "00850"}
    r = match_invoice([JONES, twin], "JONES ELEMENTARY SCHOOL PTA", 408.10)
    assert r.invoice is None
    assert r.needs_choice
    assert "equally well" in r.reason


def test_no_open_invoices_says_so_plainly():
    r = match_invoice([], "Anyone", 100.0)
    assert r.invoice is None
    assert "no unpaid invoices" in r.reason.lower()


def test_candidates_come_back_so_a_person_can_choose():
    r = match_invoice([JONES], "Someone Unrelated", 12.00)
    assert r.candidates and r.candidates[0].flags


# ── what the check settles ───────────────────────────────────────────────────

def test_a_check_for_the_fee_free_figure_settles_in_full():
    plan = build_settle_plan(THRIFTBOOKS, 131.44,
                             fee_free_total=FEE_FREE_THRIFTBOOKS)
    assert plan.cc_fee_removed == 4.96
    assert plan.amount_due_after_fee == 131.44
    assert plan.status == "exact"
    assert plan.fully_paid
    assert plan.settles_cleanly
    assert plan.payment_method == "CHECK"


def test_the_fee_is_taken_off_the_amount_due():
    """The whole point: the client owes $131.44, not the $136.40 the invoice
    says, because there was no card to process."""
    plan = build_settle_plan(THRIFTBOOKS, 131.44,
                             fee_free_total=FEE_FREE_THRIFTBOOKS)
    assert plan.invoice_total == 136.40
    assert plan.amount_due_after_fee < plan.invoice_total


def test_a_short_check_is_a_part_payment_not_a_settlement():
    plan = build_settle_plan(THRIFTBOOKS, 100.00,
                             fee_free_total=FEE_FREE_THRIFTBOOKS)
    assert plan.status == "underpaid"
    assert not plan.fully_paid
    assert plan.variance == -31.44
    assert any("short of" in w for w in plan.warnings)


def test_an_overpayment_is_flagged_but_still_settles():
    plan = build_settle_plan(THRIFTBOOKS, 150.00,
                             fee_free_total=FEE_FREE_THRIFTBOOKS)
    assert plan.status == "overpaid"
    assert plan.fully_paid
    assert plan.variance == 18.56          # 150.00 − 131.44
    assert any("more than" in w for w in plan.warnings)


def test_a_cent_of_rounding_is_not_an_underpayment():
    """A one-cent gap must not put an event on Needs Attention. This is why the
    fee-free total is computed by the billing engine and passed in, rather than
    peeled off as 4% here."""
    plan = build_settle_plan(THRIFTBOOKS, 131.45,
                             fee_free_total=FEE_FREE_THRIFTBOOKS)
    assert plan.status == "exact"
    assert plan.fully_paid


def test_the_plan_is_derivable_from_the_calculations_block():
    plan = build_settle_plan(
        THRIFTBOOKS, 131.44,
        calc={"FINAL_INVOICE_AMOUNT": 136.40, "CC_FEE": 4.96},
    )
    assert plan.amount_due_after_fee == 131.44
    assert plan.status == "exact"


def test_an_invoice_that_never_had_a_fee_says_so():
    no_fee = {**THRIFTBOOKS, "grandTotal": 131.44}
    plan = build_settle_plan(no_fee, 131.44, fee_free_total=131.44)
    assert plan.cc_fee_removed == 0
    assert any("no 4% processing fee" in w for w in plan.warnings)


def test_a_missing_fee_free_figure_is_warned_about_not_guessed():
    """Better to say "this still includes the fee, check it" than to invent a
    number by subtracting 4% from a total that was taxed after the fee."""
    plan = build_settle_plan(THRIFTBOOKS, 136.40)
    assert plan.amount_due_after_fee == 136.40
    assert any("still includes it" in w for w in plan.warnings)


def test_an_invoice_with_no_event_warns_that_event_fields_stay_put():
    orphan = {**THRIFTBOOKS, "eventId": ""}
    plan = build_settle_plan(orphan, 131.44, fee_free_total=FEE_FREE_THRIFTBOOKS)
    assert any("isn't linked to an event" in w for w in plan.warnings)


# ── the memo says which events a cheque is for ───────────────────────────────

def test_memo_dates_are_read_and_outrank_the_cheques_own_date():
    """From production: a cheque dated 2026-07-24 with the memo "Kona Ice on 7/9
    and 7/21". The date in the corner is when somebody sat down with the
    chequebook; the memo states which events are being paid for."""
    from app.core.check_settlement import memo_dates

    assert memo_dates("Kona Ice on 7/9 and 7/21", "2026-07-24") == [
        "2026-07-09", "2026-07-21"]
    assert memo_dates("July 9 and July 21", "2026-07-24") == [
        "2026-07-09", "2026-07-21"]
    # A bare M/D takes the cheque's year, stepping back when that would put the
    # event in the future — a memo describes what has already happened.
    assert memo_dates("event 12/20", "2026-01-05") == ["2025-12-20"]
    assert memo_dates("Teacher Appreciation Week", "2026-05-04") == []
    assert memo_dates("", "2026-05-04") == []


def test_an_invoice_the_memo_names_outscores_one_merely_near_the_cheque_date():
    """Two events for one customer: the memo names the first, the cheque was
    written nearer the second. The memo wins — it is a statement, not a
    coincidence of timing."""
    named = {**JONES, "id": "inv-named"}
    nearer = {**JONES, "id": "inv-nearer", "invoiceNumber": "00851"}
    r = match_invoice(
        [named, nearer], "JONES ELEMENTARY SCHOOL PTA", 9999.00,
        check_date="2026-07-24", memo="Kona Ice on 7/9",
        event_meta={
            "inv-named": {"event_name": "Field Day", "event_date": "2026-07-09"},
            "inv-nearer": {"event_name": "Fun Run", "event_date": "2026-07-22"},
        },
    )
    assert r.invoice_id == "inv-named"
    assert any("memo names 2026-07-09" in f for f in r.candidates[0].flags)
