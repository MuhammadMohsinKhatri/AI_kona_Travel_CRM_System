"""Applying a check to an invoice, and a spoken cash total to an event.

The behaviours here are the ones where being wrong costs real money — a payment
recorded against the wrong customer, a check recorded twice, or a client chased
for a cent they don't owe — so each has a test naming the failure it prevents.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_konaice.db")
os.environ.setdefault("PIPELINE_RUN_INLINE", "true")
os.environ.setdefault("MOCK_LATENCY_S", "0")
os.environ["CRM_PROVIDER"] = "mock"
os.environ["SQUARE_PROVIDER"] = "mock"
os.environ["OPENAI_PROVIDER"] = "mock"
os.environ["TELEGRAM_PROVIDER"] = "mock"
os.environ["PIPELINE_DRY_RUN"] = "false"

from typing import Any  # noqa: E402

from app.core import intake_service as svc  # noqa: E402
from app.core.intake_readers import CashEntry, CheckRead  # noqa: E402
from app.db.base import Base, SessionLocal, engine  # noqa: E402
from app.models import (CrmAuditEntry, Event, FinancialEntry,  # noqa: E402
                        Invoice)

# ThriftBooks as it actually stands: 62 servings at $2.00 = $124.00,
# +6% tax = $7.44, +4% processing fee = $4.96 → $136.40.
# Take the fee off and the client owes $131.44 — the figure the office quotes,
# and therefore the figure a correctly-written check carries.
WITH_FEE = 136.40
WITHOUT_FEE = 131.44

CLASSIFICATION = {
    "EVENT_ID": "ev-thrift",
    "EVENT_NAME": "ThriftBooks",
    "EVENT_TYPE": "PACKAGE",
    "BILLING_MODEL": "PACKAGE_PER_SERVING",
    "UNITS_SERVED_TOTAL": 62,
    "RATE_PER_SERVING": 2.00,
    "TAXABLE": "YES",
    "PAYMENT_METHOD": "CHECK",
    "PAID_STATUS": "FALSE",
    "CONTACT_EMAIL": "office@thriftbooks.example",
}


def setup_module(_):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


class FakeCRM:
    """A CRM that records what it was told to do, so the test can assert on the
    writes rather than on our own copy of them."""

    def __init__(self, invoices: list[dict[str, Any]]):
        self.invoices = invoices
        self.updated: list[dict[str, Any]] = []
        self.paid: list[dict[str, Any]] = []

    def list_invoices(self) -> list[dict[str, Any]]:
        return self.invoices

    def update_invoice(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.updated.append(payload)
        return {"ok": True}

    def mark_invoice_paid(self, invoice_id, *, paid_amount, partial=False, note=""):
        self.paid.append({"invoice_id": invoice_id, "paid_amount": paid_amount,
                          "partial": partial, "note": note})
        return {"ok": True}


def _seed(db, *, crm_event_id="ev-thrift", crm_invoice_id="inv-thrift",
          classification=None, event_date="2026-07-25", name="ThriftBooks",
          ledger=True, raw=None):
    """One processed event with the draft invoice it produced."""
    from app.core.billing import calculate_invoice
    from app.core.invoice_builder import build_invoice_payload

    cls = dict(classification or CLASSIFICATION)
    cls["EVENT_ID"] = crm_event_id
    cls["EVENT_NAME"] = name
    calc = calculate_invoice(cls)
    cleaned = {"EVENT_NAME": name, "DATE": event_date, "LOCATION": "1 Main St, Pikesville, MD, 21208"}
    raw_event = raw if raw is not None else {"id": crm_event_id, "name": name,
                                             "city": "Pikesville", "zipCode": "21208"}
    payload = build_invoice_payload({**cls, "calculations": calc}, cleaned, raw_event)

    event = Event(
        crm_event_id=crm_event_id, event_name=name, event_date=event_date,
        brand="Kona Ice", event_type="PACKAGE", billing_model=cls["BILLING_MODEL"],
        status="processed", raw=raw_event, cleaned=cleaned,
        classification=cls, calculations=calc,
        final_invoice_amount=calc["FINAL_INVOICE_AMOUNT"],
    )
    db.add(event)
    db.flush()

    invoice = Invoice(
        event_id=event.id, crm_invoice_id=crm_invoice_id,
        invoice_number=payload.get("invoiceNumber"), title=name, status="draft",
        grand_total=calc["FINAL_INVOICE_AMOUNT"], subtotal=calc["SUBTOTAL"],
        payload=payload,
    )
    db.add(invoice)
    if ledger:
        db.add(FinancialEntry(
            event_id=event.id, crm_event_id=crm_event_id, event_name=name,
            event_date=event_date, billing_model=cls["BILLING_MODEL"],
        ))
    db.commit()

    return event, {
        "id": crm_invoice_id,
        "invoiceNumber": payload.get("invoiceNumber"),
        "eventId": crm_event_id,
        "businessName": name,
        "grandTotal": calc["FINAL_INVOICE_AMOUNT"],
        "invoiceStatus": "draft",
    }


def _fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    return SessionLocal()


# ── the fee-free figure ──────────────────────────────────────────────────────

def test_fee_free_total_comes_from_the_engine_not_from_subtracting_4_percent():
    """$136.40 less 4% is $130.94; the right answer is $131.44.

    The fee is charged on the PRE-TAX subtotal, so arithmetic on the grand total
    is wrong by half a dollar here — and by a cent in the ordinary case, which
    is enough to turn a check that paid in full into "underpaid by $0.01".
    """
    db = _fresh_db()
    try:
        _seed(db)
        assert svc.fee_free_total(db, "inv-thrift") == WITHOUT_FEE
        assert round(WITH_FEE * 0.96, 2) != WITHOUT_FEE   # the tempting shortcut
    finally:
        db.close()


def test_no_fee_free_figure_when_the_total_was_stated_rather_than_calculated():
    """An amount typed in the notes overrides the engine, so waiving the fee
    changes nothing. Reporting "no fee to remove" there would be a guess — say
    we can't work it out instead."""
    db = _fresh_db()
    try:
        _seed(db, classification={**CLASSIFICATION,
                                  "CHECK_INVOICE_AMOUNT": 136.40})
        assert svc.fee_free_total(db, "inv-thrift") is None
    finally:
        db.close()


# ── reviewing a check ────────────────────────────────────────────────────────

def test_a_check_for_the_fee_free_amount_reads_as_paid_in_full():
    """The normal case. If this scored as an underpayment, every correctly
    written check would park its event on Needs Attention."""
    db = _fresh_db()
    try:
        _, inv = _seed(db)
        crm = FakeCRM([inv])
        review = svc.review_check(
            db, crm, CheckRead(payer_name="ThriftBooks", amount=WITHOUT_FEE))

        assert review.ready
        assert review.plan.invoice_id == "inv-thrift"
        assert review.plan.status == "exact"
        assert review.plan.fully_paid
        assert review.plan.cc_fee_removed == 4.96
        assert review.plan.amount_due_after_fee == WITHOUT_FEE
    finally:
        db.close()


def test_review_writes_nothing():
    db = _fresh_db()
    try:
        _, inv = _seed(db)
        crm = FakeCRM([inv])
        svc.review_check(db, crm, CheckRead(payer_name="ThriftBooks",
                                            amount=WITHOUT_FEE))
        assert crm.updated == [] and crm.paid == []
    finally:
        db.close()


def test_an_unreadable_check_asks_for_the_details_instead_of_matching():
    db = _fresh_db()
    try:
        _, inv = _seed(db)
        review = svc.review_check(
            db, FakeCRM([inv]), CheckRead(error="Couldn't read the check: blurry"))
        assert review.plan is None
        assert review.match.needs_choice
    finally:
        db.close()


def test_an_invoice_picked_by_hand_beats_the_score():
    """The reviewer is looking at the paper. Their choice is the answer."""
    db = _fresh_db()
    try:
        _, thrift = _seed(db)
        _, jones = _seed(db, crm_event_id="ev-jones", crm_invoice_id="inv-jones",
                         name="Jones Elementary PTA")
        review = svc.review_check(
            db, FakeCRM([thrift, jones]),
            CheckRead(payer_name="ThriftBooks", amount=WITHOUT_FEE),
            invoice_id="inv-jones",
        )
        assert review.plan.invoice_id == "inv-jones"
    finally:
        db.close()


# ── applying a check ─────────────────────────────────────────────────────────

def test_applying_takes_the_fee_off_the_invoice_then_records_the_payment():
    db = _fresh_db()
    try:
        _, inv = _seed(db)
        crm = FakeCRM([inv])
        review = svc.review_check(
            db, crm, CheckRead(payer_name="ThriftBooks", amount=WITHOUT_FEE))
        result = svc.apply_check(db, crm, review.plan, by="office@example.com")

        assert result.ok
        # The invoice was edited in place — same id, no fee line, smaller total.
        assert len(crm.updated) == 1
        pushed = crm.updated[0]
        assert pushed["id"] == "inv-thrift"
        assert pushed["grandTotal"] == WITHOUT_FEE
        assert not any("Processing Fee" in i["name"]
                       for i in pushed["clientInvoiceItems"])
        # …and then paid in full, not partially.
        assert crm.paid == [{"invoice_id": "inv-thrift", "paid_amount": WITHOUT_FEE,
                             "partial": False,
                             "note": "Check 131.44 — applied by office@example.com"}]

        invoice = db.query(Invoice).one()
        assert invoice.status == "paid"
        assert invoice.grand_total == WITHOUT_FEE
        assert db.query(FinancialEntry).one().paid is True
        assert db.query(CrmAuditEntry).filter_by(action="check_applied").count() == 1
    finally:
        db.close()


def test_editing_the_invoice_never_issues_it():
    """Taking a fee off changes the figures. It must not also send a client a
    document nobody asked to send — the draft stays a draft."""
    db = _fresh_db()
    try:
        _, inv = _seed(db)
        crm = FakeCRM([inv])
        review = svc.review_check(
            db, crm, CheckRead(payer_name="ThriftBooks", amount=WITHOUT_FEE))
        svc.apply_check(db, crm, review.plan)
        assert crm.updated[0]["saveAsDraft"] is True
        assert crm.updated[0]["invoiceStatus"] == "draft"
    finally:
        db.close()


def test_a_check_already_recorded_in_konaos_is_not_recorded_again():
    """The regression this guards: the review was built minutes ago, and in the
    meantime someone keyed the same check in by hand. Applying now would take
    the payment twice."""
    db = _fresh_db()
    try:
        _, inv = _seed(db)
        crm = FakeCRM([inv])
        review = svc.review_check(
            db, crm, CheckRead(payer_name="ThriftBooks", amount=WITHOUT_FEE))

        inv["invoiceStatus"] = "paid"          # someone got there first
        result = svc.apply_check(db, crm, review.plan)

        assert not result.ok
        assert "already been marked paid" in result.summary
        assert crm.updated == [] and crm.paid == []
    finally:
        db.close()


def test_a_name_match_on_a_disagreeing_amount_is_found_but_never_applied_alone():
    """$100 against a $131.44 balance: the name fits, the money does not.

    This is the trade at the heart of matching on who-and-when. The invoice IS
    identified — refusing to would leave the office hunting for it by hand — but
    it does not settle itself, because a figure that disagrees is either a part
    payment or a misread digit, and those are indistinguishable from here.
    A person sees it, with the discrepancy spelled out.
    """
    db = _fresh_db()
    try:
        _, inv = _seed(db)
        review = svc.review_check(
            db, FakeCRM([inv]), CheckRead(payer_name="ThriftBooks", amount=100.00))

        assert review.plan is not None                 # found
        assert review.plan.invoice_id == "inv-thrift"
        assert review.plan.status == "underpaid"
        assert "31.44 short" in " ".join(review.plan.warnings)

        ok, why = svc.auto_applicable_check(review)    # but not written
        assert not ok
        assert "short" in why.lower()
    finally:
        db.close()


def test_a_short_check_leaves_the_balance_open():
    """$100 against $131.44 owed is a part payment. Marking it settled would
    close a balance the client still owes.

    Reached by hand-picking the invoice — a check for an amount matching nothing
    is exactly the case the matcher refuses to call (above)."""
    db = _fresh_db()
    try:
        _, inv = _seed(db)
        crm = FakeCRM([inv])
        review = svc.review_check(
            db, crm, CheckRead(payer_name="ThriftBooks", amount=100.00),
            invoice_id="inv-thrift")

        assert review.plan.status == "underpaid"
        assert review.plan.fully_paid is False

        svc.apply_check(db, crm, review.plan)
        assert crm.paid[0]["partial"] is True
        assert db.query(Invoice).one().status == "partially_paid"
        assert db.query(FinancialEntry).one().paid is False
    finally:
        db.close()


def test_dry_run_writes_nothing_and_says_so():
    db = _fresh_db()
    try:
        _, inv = _seed(db)
        crm = FakeCRM([inv])
        review = svc.review_check(
            db, crm, CheckRead(payer_name="ThriftBooks", amount=WITHOUT_FEE))
        result = svc.apply_check(db, crm, review.plan, dry_run=True)

        assert result.ok and result.dry_run
        assert crm.updated == [] and crm.paid == []
        assert db.query(Invoice).one().status == "draft"
    finally:
        db.close()


# ── what may settle itself, with nobody looking ──────────────────────────────

def test_an_exactly_matching_check_settles_itself():
    """The ordinary case, and the whole point: photo in, nothing typed, done."""
    db = _fresh_db()
    try:
        _, inv = _seed(db)
        review = svc.review_check(
            db, FakeCRM([inv]), CheckRead(payer_name="ThriftBooks", amount=WITHOUT_FEE))
        ok, why = svc.auto_applicable_check(review)
        assert ok, why
    finally:
        db.close()


def test_a_check_read_with_no_payer_still_matches_on_its_amount():
    """A grand total matching to the cent identifies the invoice on its own —
    and it survives OCR better than handwriting does. Refusing to match without
    a payer name threw away readable checks over the one unreadable field that
    mattered least."""
    db = _fresh_db()
    try:
        _, inv = _seed(db)
        review = svc.review_check(
            db, FakeCRM([inv]), CheckRead(payer_name="", amount=WITHOUT_FEE))
        assert review.plan is not None
        assert svc.auto_applicable_check(review)[0]
    finally:
        db.close()


def test_two_invoices_for_the_same_total_are_never_settled_automatically():
    """What makes an amount-only match safe is that it is unique. Two customers
    owing the same figure is precisely when guessing pays the wrong invoice."""
    db = _fresh_db()
    try:
        _, a = _seed(db, crm_event_id="ev-a", crm_invoice_id="inv-a", name="Acme Corp")
        _, b = _seed(db, crm_event_id="ev-b", crm_invoice_id="inv-b", name="Beta LLC")
        review = svc.review_check(
            db, FakeCRM([a, b]), CheckRead(payer_name="", amount=WITHOUT_FEE))
        assert review.plan is None
        ok, why = svc.auto_applicable_check(review)
        assert not ok and why
    finally:
        db.close()


def test_a_check_that_does_not_pay_in_full_waits_for_a_person():
    """Short or over is either a part payment or a 3 misread as an 8 — the same
    thing on screen, a very different thing in the ledger."""
    db = _fresh_db()
    try:
        _, inv = _seed(db)
        review = svc.review_check(
            db, FakeCRM([inv]), CheckRead(payer_name="ThriftBooks", amount=100.00),
            invoice_id="inv-thrift")
        ok, why = svc.auto_applicable_check(review)
        assert not ok
        assert "short" in why.lower()
    finally:
        db.close()


def test_a_check_whose_fee_cannot_be_recomputed_waits_for_a_person():
    """A warning on the plan changes what applying MEANS — here, that the
    invoice would be marked paid with the 4% still on it."""
    db = _fresh_db()
    try:
        _, inv = _seed(db, classification={**CLASSIFICATION,
                                           "CHECK_INVOICE_AMOUNT": 136.40})
        review = svc.review_check(
            db, FakeCRM([inv]), CheckRead(payer_name="ThriftBooks", amount=WITH_FEE))
        ok, why = svc.auto_applicable_check(review)
        assert not ok
        assert "4%" in why
    finally:
        db.close()


def test_an_unreadable_image_settles_nothing():
    db = _fresh_db()
    try:
        _, inv = _seed(db)
        review = svc.review_check(
            db, FakeCRM([inv]), CheckRead(error="This is not an image of a check."))
        assert not svc.auto_applicable_check(review)[0]
    finally:
        db.close()


def test_a_matched_cash_line_posts_itself_and_an_unmatched_one_does_not():
    db = _fresh_db()
    try:
        _seed(db, crm_event_id="ev-pikes", crm_invoice_id="inv-pikes",
              name="Pikesville Farmers Market", event_date="2026-07-25",
              raw={"id": "ev-pikes", "name": "Pikesville Farmers Market"})
        matched, missing = svc.review_cash(db, [
            CashEntry(query="Pikesville farmers market", amount=7.0, date="2026-07-25"),
            CashEntry(query="somewhere nobody has heard of", amount=9.0,
                      date="2026-07-25"),
        ])
        assert svc.auto_applicable_cash(matched)[0]
        ok, why = svc.auto_applicable_cash(missing)
        assert not ok and why
    finally:
        db.close()


# ── cash ─────────────────────────────────────────────────────────────────────

def test_a_spoken_phrase_matches_the_event_it_names():
    db = _fresh_db()
    try:
        _seed(db, crm_event_id="ev-pikes", crm_invoice_id="inv-pikes",
              name="Pikesville Farmers Market", event_date="2026-07-25",
              raw={"id": "ev-pikes", "name": "Pikesville Farmers Market",
                   "city": "Pikesville", "zipCode": "21208"})
        [review] = svc.review_cash(
            db, [CashEntry(query="Pikesville farmers market", amount=7.0,
                           date="2026-07-25")])

        assert review.ready
        assert review.event.crm_event_id == "ev-pikes"
        assert review.previous_cash == 0.0
    finally:
        db.close()


def test_cash_for_an_event_we_have_not_processed_is_blocked_not_guessed():
    """No ledger row means nothing to post to. Saying so beats matching it to
    whatever else happens to be on that date."""
    db = _fresh_db()
    try:
        _seed(db, crm_event_id="ev-pikes", crm_invoice_id="inv-pikes",
              name="Pikesville Farmers Market", event_date="2026-07-25",
              ledger=False,
              raw={"id": "ev-pikes", "name": "Pikesville Farmers Market"})
        [review] = svc.review_cash(
            db, [CashEntry(query="Pikesville farmers market", amount=7.0,
                           date="2026-07-25")])

        assert not review.ready
        assert "hasn't been processed" in review.blocked
    finally:
        db.close()


def test_two_events_matching_equally_well_ask_rather_than_pick():
    db = _fresh_db()
    try:
        for i, code in enumerate(("a", "b")):
            _seed(db, crm_event_id=f"ev-{code}", crm_invoice_id=f"inv-{code}",
                  name="Milford Mill Elementary", event_date="2026-07-25",
                  raw={"id": f"ev-{code}", "name": "Milford Mill Elementary"})
        [review] = svc.review_cash(
            db, [CashEntry(query="Milford Mill", amount=25.0, date="2026-07-25")])

        assert not review.ready
        assert review.match.needs_choice
        assert len(review.match.candidates) >= 2
    finally:
        db.close()


def test_an_amount_heard_with_no_event_is_kept_for_a_person_to_place():
    """The workflow's own hard case: the admin said a figure but nothing
    identifiable. Dropping it loses money quietly."""
    db = _fresh_db()
    try:
        _seed(db, crm_event_id="ev-pikes", crm_invoice_id="inv-pikes",
              name="Pikesville Farmers Market", event_date="2026-07-25",
              raw={"id": "ev-pikes", "name": "Pikesville Farmers Market"})
        [review] = svc.review_cash(
            db, [CashEntry(query="", amount=12.50, date="2026-07-25")])

        assert not review.ready
        assert review.entry.amount == 12.50
    finally:
        db.close()


def test_an_event_picked_by_hand_is_matched_directly():
    db = _fresh_db()
    try:
        _seed(db, crm_event_id="ev-pikes", crm_invoice_id="inv-pikes",
              name="Pikesville Farmers Market", event_date="2026-07-25",
              raw={"id": "ev-pikes", "name": "Pikesville Farmers Market"})
        review = svc.review_cash_for_event(
            db, "ev-pikes", CashEntry(query="the one on Tuesday", amount=7.0))

        assert review.ready
        assert review.event.crm_event_id == "ev-pikes"
    finally:
        db.close()


# ── the wiring the upload actually goes through ──────────────────────────────

def test_uploading_a_check_records_it_without_anything_else_being_pressed():
    """End to end through the real route: photo in, invoice paid, fee off.

    The policy above decides; this proves the endpoint acts on it. The vision
    call is stubbed because the point under test is the wiring, not OCR.
    """
    from fastapi.testclient import TestClient

    from app.api.deps import get_current_user
    from app.api.routes import intake
    from app.db.base import get_db
    from app.integrations.factory import get_crm
    from app.main import app
    from app.models import User

    db = _fresh_db()
    try:
        _, inv = _seed(db)
        crm = FakeCRM([inv])

        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: User(
            id=1, email="office@example.com", hashed_password="x", is_active=True)
        real_read, real_crm = intake.read_check, intake.get_crm
        intake.read_check = lambda *a, **k: CheckRead(
            payer_name="ThriftBooks", amount=WITHOUT_FEE, confidence="high")
        intake.get_crm = lambda: crm
        try:
            client = TestClient(app)
            body = client.post(
                "/api/intake/check",
                files={"file": ("check.jpg", b"not-really-a-jpeg", "image/jpeg")},
            ).json()
        finally:
            intake.read_check, intake.get_crm = real_read, real_crm
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)
            get_crm.cache_clear()

        assert body["applied"]["ok"] is True
        assert "4% processing fee" in body["applied"]["summary"]
        assert crm.paid[0]["partial"] is False
        assert db.query(Invoice).one().status == "paid"
    finally:
        db.close()


# ── voice memos off a phone ──────────────────────────────────────────────────

def test_a_phone_voice_memo_keeps_its_own_format():
    """The extension is how the format is declared to the transcriber. Calling
    an iPhone's .m4a "speech.webm" makes an unreadable file out of a fine one —
    and uploading a memo is the only way in to this feature while the dashboard
    is served over plain http."""
    from app.api.routes.intake import _audio_name

    assert _audio_name("Voice 004.m4a") == "Voice 004.m4a"
    assert _audio_name("takings.ogg") == "takings.ogg"
    assert _audio_name("") == "speech.webm"


def test_audio_we_cannot_transcribe_is_refused_in_words_not_an_api_error():
    from app.api.routes.intake import _audio_name

    assert _audio_name("old-nokia.amr") == ""
    assert _audio_name("check.jpg") == ""


# ── a date the model got wrong must not become "your event doesn't exist" ────

def test_a_wrong_date_widens_to_the_recent_window_instead_of_dead_ending():
    """Reported from production: "add cash in Arbutus food truck of 29th July,
    $63" answered "No events on that date to match against" — while Arbutus
    Food Truck sat on 2026-07-29 in the ledger.

    The sentence never carried a year, and the prompt never said what today was,
    so the model resolved "29th July" against nothing and picked a year of its
    own. Telling somebody their event doesn't exist, because of a year they
    never said, is both wrong and unactionable.
    """
    db = _fresh_db()
    try:
        from datetime import date as _date
        today = _date.today().isoformat()
        _seed(db, crm_event_id="ev-arb", crm_invoice_id="inv-arb",
              name="Arbutus Food Truck", event_date=today,
              raw={"id": "ev-arb", "name": "Arbutus Food Truck"})

        [review] = svc.review_cash(db, [CashEntry(
            query="Arbutus food truck", amount=63.0, date="2024-07-29")])

        assert review.event is not None, review.match.reason
        assert review.event.crm_event_id == "ev-arb"
        assert "looked across the last" in review.match.reason
    finally:
        db.close()


def test_the_speech_prompt_states_todays_date():
    """The one thing a model cannot infer from the sentence. Without it,
    "29th July" has no year and any answer is a guess."""
    from app.core.intake_readers import _SPEECH_PROMPT

    filled = _SPEECH_PROMPT.format(today="2026-07-31")
    assert "TODAY IS 2026-07-31" in filled
    assert "never resolve a date into the future" in filled
    # The JSON shape must survive .format() — doubled braces, not stray ones.
    assert '"entries": [' in filled and '{"query"' in filled


def test_the_transcriber_is_asked_to_translate_not_merely_transcribe():
    """From production: a sentence spoken with an Urdu accent came back as
    "ارگوٹھ سوڈ ٹرک" — a faithful transcript of "Arbutus Food Truck" and a
    useless one, because every event name in KonaOS is English and the matcher
    scored name+0 against all of them.

    Translation always emits English; English in gives English out unchanged, so
    a native speaker loses nothing.
    """
    import inspect

    from app.core import intake_readers

    source = inspect.getsource(intake_readers.transcribe)
    assert "audio.translations.create" in source
    assert "audio.transcriptions.create" not in source


def test_the_speech_prompt_requires_an_english_query():
    from app.core.intake_readers import _SPEECH_PROMPT

    filled = _SPEECH_PROMPT.format(today="2026-07-31")
    assert "query MUST be English" in filled
    assert "Arbutus Food Truck" in filled       # the transliteration example


# ── the CRM adapter must never be driven from the event loop ─────────────────

def test_uploading_a_check_does_not_call_the_crm_from_the_event_loop():
    """From production: every check upload 500'd while every test passed.

    The KonaOS adapter is a synchronous wrapper that drives its own asyncio loop
    with run_until_complete. An `async def` route runs on the event loop thread,
    and Python refuses to start a loop inside a running one — so the real CRM
    raised "Cannot run the event loop while another loop is running" on every
    upload. MockCRMClient has no loop, so nothing caught it.

    This CRM stands in for that: it fails if it is ever called from a thread with
    a running loop, which is exactly the condition the real one cannot survive.
    """
    import asyncio

    from fastapi.testclient import TestClient

    from app.api.deps import get_current_user
    from app.api.routes import intake
    from app.db.base import get_db
    from app.integrations.factory import get_crm
    from app.main import app
    from app.models import User

    class LoopHostileCRM(FakeCRM):
        def list_invoices(self):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return super().list_invoices()      # good: no loop on this thread
            raise RuntimeError(
                "Cannot run the event loop while another loop is running")

    db = _fresh_db()
    try:
        _, inv = _seed(db)
        crm = LoopHostileCRM([inv])

        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: User(
            id=1, email="office@example.com", hashed_password="x", is_active=True)
        real_read, real_crm = intake.read_check, intake.get_crm
        intake.read_check = lambda *a, **k: CheckRead(
            payer_name="ThriftBooks", amount=WITHOUT_FEE, confidence="high")
        intake.get_crm = lambda: crm
        try:
            response = TestClient(app).post(
                "/api/intake/check",
                files={"file": ("check.jpg", b"not-really-a-jpeg", "image/jpeg")},
            )
        finally:
            intake.read_check, intake.get_crm = real_read, real_crm
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)
            get_crm.cache_clear()

        assert response.status_code == 200, response.text
        assert response.json()["applied"]["ok"] is True
    finally:
        db.close()


# ── the sheet importer reads the whole workbook, not one tab ─────────────────

def test_workbook_tab_discovery_falls_back_to_the_url_it_was_given():
    """Discovery is a scrape of Google's HTML, so it will break one day. When it
    does, importing the configured month beats importing nothing — and the
    response reports tabs_read so a silent fallback is still visible."""
    from unittest.mock import patch

    import httpx as _httpx

    from app.api.routes.financials import _workbook_tab_urls

    url = ("https://docs.google.com/spreadsheets/d/SHEETID/export"
           "?format=csv&gid=123")

    with patch.object(_httpx, "get", side_effect=_httpx.HTTPError("no network")):
        assert _workbook_tab_urls(url) == [url]

    class _Resp:
        text = 'href="…gid=111" … href="…gid=222" … href="…gid=111"'

        def raise_for_status(self):
            return None

    with patch.object(_httpx, "get", return_value=_Resp()):
        found = _workbook_tab_urls(url)
    assert [u.rsplit("gid=", 1)[-1] for u in found] == ["111", "222"]
    assert all("/spreadsheets/d/SHEETID/export?format=csv&gid=" in u for u in found)


def test_a_url_that_is_not_a_workbook_is_left_alone():
    from app.api.routes.financials import _workbook_tab_urls

    assert _workbook_tab_urls("https://example.com/data.csv") == [
        "https://example.com/data.csv"]


def test_a_settled_check_names_the_event_not_just_the_invoice():
    """"Invoice 00084 is paid" is only checkable by someone willing to go and
    look it up. "ThriftBooks, 2026-07-25" is recognised on sight by whoever
    booked it — and it is how you catch a payment landing on the wrong job."""
    db = _fresh_db()
    try:
        _, inv = _seed(db)
        crm = FakeCRM([inv])
        review = svc.review_check(
            db, crm, CheckRead(payer_name="ThriftBooks", amount=WITHOUT_FEE))

        assert review.plan.event_name == "ThriftBooks"
        assert review.plan.event_date == "2026-07-25"

        payload = svc.check_review_json(review)
        assert payload["plan"]["event_name"] == "ThriftBooks"
        assert payload["plan"]["event_date"] == "2026-07-25"

        result = svc.apply_check(db, crm, review.plan, by="office@example.com")
        assert "ThriftBooks" in result.summary and "2026-07-25" in result.summary
    finally:
        db.close()


def test_cash_candidates_carry_the_event_date_not_the_town():
    """Reported from production: the cash candidate table printed "Milford Mill"
    and "Baltimore" under a heading that said DATE.

    KonaOS dates an event with `startDateTime` in epoch ms — nothing in the
    payload is called `eventDate` — so the date read as "" for every candidate
    and the screen fell back to the town. A town under a DATE heading reads as
    data and is simply false.
    """
    db = _fresh_db()
    try:
        # Two same-named events so the matcher refuses and returns candidates.
        for code in ("a", "b"):
            _seed(db, crm_event_id=f"ev-{code}", crm_invoice_id=f"inv-{code}",
                  name="Milford Mill Elementary", event_date="2026-07-25",
                  raw={"id": f"ev-{code}", "name": "Milford Mill Elementary",
                       "city": "Milford Mill",
                       # KonaOS's real shape: epoch ms, and no eventDate key.
                       "startDateTime": 1785024000000})
        [review] = svc.review_cash(
            db, [CashEntry(query="Milford Mill", amount=25.0, date="2026-07-25")])

        assert review.match.candidates, "expected candidates to choose between"
        rows = [svc._candidate_json(c) for c in review.match.candidates]
        for row in rows:
            assert row["event_date"] == "2026-07-25", row
            assert row["city"] == "Milford Mill"
            assert row["event_date"] != row["city"]
    finally:
        db.close()


def test_a_cheque_for_an_already_paid_invoice_says_so_with_the_fee_free_figure():
    """Brett, holding a cheque: "this one is paid, it should show that it's
    already paid — and minus the card fee for data entry."

    Settled invoices used to be dropped before scoring, so this reported "no
    unpaid invoice matches" — true, and useless. It now names the invoice, its
    event, and what was actually payable by cheque once the 4% came off.
    """
    db = _fresh_db()
    try:
        _, inv = _seed(db)
        inv["invoiceStatus"] = "paid"          # settled in KonaOS
        review = svc.review_check(
            db, FakeCRM([inv]), CheckRead(payer_name="ThriftBooks", amount=WITHOUT_FEE))

        assert review.plan is None                       # never re-settled
        assert review.match.settled is not None
        assert "already marked paid" in review.match.reason.lower()

        payload = svc.check_review_json(review)
        paid = payload["already_paid"]
        assert paid["event_name"] == "ThriftBooks"
        assert paid["event_date"] == "2026-07-25"
        assert paid["grand_total"] == WITH_FEE
        assert paid["total_without_fee"] == WITHOUT_FEE   # the fee-free figure

        assert not svc.auto_applicable_check(review)[0]
    finally:
        db.close()


# ── one cheque, several invoices ─────────────────────────────────────────────

def test_a_cheque_covering_two_events_is_split_across_both_invoices():
    """Brett's Leaps Ahead cheque: $530, memo "Kona Ice on 7/9 and 7/21".

    Scoring invoices one at a time can never answer this — both halves match
    equally, so it ties and asks, and the true answer is "both". $300 + $230 is
    arithmetic, not inference, which makes it the strongest signal available
    after a printed invoice number.
    """
    db = _fresh_db()
    try:
        _, a = _seed(db, crm_event_id="ev-a", crm_invoice_id="inv-a",
                     name="Leaps Ahead Learning", event_date="2026-07-09")
        _, b = _seed(db, crm_event_id="ev-b", crm_invoice_id="inv-b",
                     name="Leaps Ahead Learning", event_date="2026-07-21")
        # Both invoices are $136.40 with the fee, $131.44 without → $262.88.
        review = svc.review_check(
            db, FakeCRM([a, b]),
            CheckRead(payer_name="Leaps Ahead Learning LLC",
                      amount=WITHOUT_FEE * 2, check_date="2026-07-24",
                      memo="Kona Ice on 7/9 and 7/21"))

        assert review.plan is None                  # not one invoice
        assert len(review.split) == 2               # but two, together
        assert {p.invoice_id for p in review.split} == {"inv-a", "inv-b"}
        assert all(p.status == "exact" for p in review.split)
        assert all(p.cc_fee_removed == 4.96 for p in review.split)

        payload = svc.check_review_json(review)
        assert payload["split_total"] == WITHOUT_FEE * 2
        assert {p["event_date"] for p in payload["split"]} == {
            "2026-07-09", "2026-07-21"}
    finally:
        db.close()


def test_an_invoice_number_on_the_cheque_settles_the_match_outright():
    """The remittance slip on a real cheque printed INVOICE NUMBER 00084. That
    is a key, not a signal: an exact hit ends the argument."""
    db = _fresh_db()
    try:
        _, a = _seed(db, crm_event_id="ev-a", crm_invoice_id="inv-a", name="Acme")
        _, b = _seed(db, crm_event_id="ev-b", crm_invoice_id="inv-b", name="Acme")
        a["invoiceNumber"] = "00084"
        b["invoiceNumber"] = "00085"

        # Identical names and totals — hopeless without the number, decided with it.
        review = svc.review_check(
            db, FakeCRM([a, b]),
            CheckRead(payer_name="Acme", amount=WITHOUT_FEE, invoice_number="00084"))

        assert review.plan is not None
        assert review.plan.invoice_id == "inv-a"
        assert any("invoice#" in f for f in review.match.candidates[0].flags)
    finally:
        db.close()


def test_applying_a_split_cheque_settles_every_invoice_in_it():
    """Each part is settled for what THAT invoice is worth after its own 4% —
    not for a share of the cheque divided by hand. That is what makes the parts
    add up to the cheque instead of merely near it."""
    from fastapi.testclient import TestClient

    from app.api.deps import get_current_user
    from app.api.routes import intake
    from app.db.base import get_db
    from app.integrations.factory import get_crm
    from app.main import app
    from app.models import User

    db = _fresh_db()
    try:
        _, a = _seed(db, crm_event_id="ev-a", crm_invoice_id="inv-a",
                     name="Leaps Ahead Learning", event_date="2026-07-09")
        _, b = _seed(db, crm_event_id="ev-b", crm_invoice_id="inv-b",
                     name="Leaps Ahead Learning", event_date="2026-07-21")
        crm = FakeCRM([a, b])

        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: User(
            id=1, email="office@example.com", hashed_password="x", is_active=True)
        real_crm = intake.get_crm
        intake.get_crm = lambda: crm
        try:
            body = TestClient(app).post("/api/intake/apply", json={"items": [{
                "kind": "check", "amount": WITHOUT_FEE * 2,
                "invoice_ids": ["inv-a", "inv-b"],
                "payer_name": "Leaps Ahead Learning LLC",
            }]}).json()
        finally:
            intake.get_crm = real_crm
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)
            get_crm.cache_clear()

        assert body["applied"] == 1, body
        assert "split across 2 of 2" in body["results"][0]["summary"]
        # Both invoices paid, each for its own fee-free total.
        assert {p["invoice_id"] for p in crm.paid} == {"inv-a", "inv-b"}
        assert all(p["paid_amount"] == WITHOUT_FEE for p in crm.paid)
        assert all(p["partial"] is False for p in crm.paid)
        assert {i.status for i in db.query(Invoice).all()} == {"paid"}
    finally:
        db.close()


# ── a fee is only removed when one was actually charged ──────────────────────

def test_no_fee_is_removed_from_an_invoice_that_never_carried_one():
    """From production, on a $250 invoice showing "CC fee waived / CC fee $0.00":

        Invoice in KonaOS now    $250.00
        Less the 4% card fee     -$27.40
        Client owes              $222.60

    $27.40 is not 4% of $250 — it is not 4% of anything on that document. The
    fee-free figure was recomputed and the DIFFERENCE from KonaOS's total was
    labelled a card fee. Most check-paid events carry no fee at all: the
    classifier reads "paid by check" in the notes and the engine skips it at
    drafting time.
    """
    db = _fresh_db()
    try:
        # PAID_STATUS TRUE + PAYMENT_METHOD CHECK → the engine never adds the fee.
        _, inv = _seed(db, classification={**CLASSIFICATION, "PAID_STATUS": "TRUE"})
        billed = inv["grandTotal"]

        assert svc.fee_free_total(db, "inv-thrift", billed) == billed

        review = svc.review_check(
            db, FakeCRM([inv]), CheckRead(payer_name="ThriftBooks", amount=billed))
        assert review.plan.cc_fee_removed == 0
        assert review.plan.amount_due_after_fee == billed
        assert review.plan.status == "exact"
        # A note, not a warning — so it still settles itself.
        assert review.plan.warnings == []
        assert svc.auto_applicable_check(review)[0]
    finally:
        db.close()


def test_no_fee_free_figure_when_our_recompute_disagrees_with_the_invoice():
    """The guard that would have caught the $27.40 outright.

    If re-running the classification doesn't land on the grand total KonaOS
    holds, the invoice was built from something we can no longer reconstruct —
    an edit in KonaOS, a since-changed note — and the gap between the two
    figures is not a processing fee. Declining beats presenting a wrong number
    under a confident label.
    """
    db = _fresh_db()
    try:
        _, inv = _seed(db)
        # KonaOS holds a different total from anything our classification makes.
        assert svc.fee_free_total(db, "inv-thrift", 250.00) is None

        inv["grandTotal"] = 250.00
        review = svc.review_check(
            db, FakeCRM([inv]), CheckRead(payer_name="ThriftBooks", amount=250.00))
        assert review.plan.cc_fee_removed == 0
        assert any("couldn't recompute" in w.lower() for w in review.plan.warnings)
        assert not svc.auto_applicable_check(review)[0]   # a person looks
    finally:
        db.close()
