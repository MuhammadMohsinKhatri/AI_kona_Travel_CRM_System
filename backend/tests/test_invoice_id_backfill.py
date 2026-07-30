"""Backfilling the KonaOS invoice id onto invoices stored without one.

Why this exists: the Invoices page showed 57 drafts and not one of them offered
a "Kona OS" link, because KonaOS's invoice-create response does not reliably
carry the new id — every row was stored with crm_invoice_id empty. The record
said "we created an invoice" without saying which one, so there was nothing to
deep-link to and nothing to mark paid against.

The create path now resolves the id itself; this repairs what is already stored
and stays on as the safety net.
"""
import os

os.environ.setdefault("CRM_PROVIDER", "mock")

import pytest

from app.db.base import Base, SessionLocal, engine
from app.models import Event, Invoice

Base.metadata.create_all(bind=engine)

pytest.importorskip("celery")

from app.tasks import watch_tasks  # noqa: E402

KOS_EVENT = "be6ba87e37b648f99aee4f7d620e64a2"
KOS_INVOICE = "a89b7adbdd1b4790bf3adeb67a195fe4"


class _Crm:
    def __init__(self, invoices, fail=False):
        self.invoices = invoices
        self.fail = fail
        self.calls = 0

    def list_invoices(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("KonaOS API error 401")
        return self.invoices


def _seed(db, *, crm_invoice_id=None, invoice_number="K530X9179333",
          crm_event_id=KOS_EVENT):
    event = Event(
        crm_event_id=crm_event_id, event_name="ThriftBooks", brand="Kona Ice",
        event_date="2026-07-29", final_status="Confirmed", status="processed",
        status_reason="", raw={}, cleaned={}, classification={}, square={},
        calculations={},
    )
    db.add(event)
    db.flush()
    inv = Invoice(
        event_id=event.id, crm_invoice_id=crm_invoice_id,
        invoice_number=invoice_number, title="ThriftBooks",
        invoice_type="Invoice", status="draft", grand_total=136.40,
        subtotal=124.0, tax_amount=7.44, due_amount=136.40, payload={},
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return event, inv


def _clear(db):
    db.query(Invoice).delete()
    db.query(Event).delete()
    db.commit()


def _run(monkeypatch, crm, **kwargs):
    monkeypatch.setattr(watch_tasks.factory, "get_crm", lambda: crm)
    return watch_tasks.backfill_invoice_ids(**kwargs)


def test_a_missing_id_is_filled_from_the_event_key(monkeypatch):
    db = SessionLocal()
    try:
        _clear(db)
        _, inv = _seed(db)
        crm = _Crm([{"id": KOS_INVOICE, "eventId": KOS_EVENT,
                     "invoiceNumber": "K530X9179333"}])

        result = _run(monkeypatch, crm)

        assert result["filled"] == 1
        db.refresh(inv)
        assert inv.crm_invoice_id == KOS_INVOICE
    finally:
        _clear(db)
        db.close()


def test_rows_that_already_have_an_id_are_not_touched(monkeypatch):
    """Idempotent, so it converges to doing nothing rather than re-writing every
    row every hour."""
    db = SessionLocal()
    try:
        _clear(db)
        _, inv = _seed(db, crm_invoice_id=KOS_INVOICE)
        crm = _Crm([{"id": "something-else", "eventId": KOS_EVENT}])

        result = _run(monkeypatch, crm)

        assert result == {"missing": 0, "filled": 0, "unresolved": 0}
        assert crm.calls == 0, "nothing missing means no KonaOS call at all"
        db.refresh(inv)
        assert inv.crm_invoice_id == KOS_INVOICE
    finally:
        _clear(db)
        db.close()


def test_one_konaos_call_covers_every_row(monkeypatch):
    """The session doesn't tolerate bursts — a per-row lookup is exactly the
    pattern to avoid, so the list is fetched once and indexed."""
    db = SessionLocal()
    try:
        _clear(db)
        rows, kos = [], []
        for i in range(8):
            _, inv = _seed(db, crm_event_id=f"ev-{i}", invoice_number=f"K-{i}")
            rows.append(inv)
            kos.append({"id": f"kos-{i}", "eventId": f"ev-{i}", "invoiceNumber": f"K-{i}"})
        crm = _Crm(kos)

        result = _run(monkeypatch, crm)

        assert result["filled"] == 8
        assert crm.calls == 1
    finally:
        _clear(db)
        db.close()


def test_an_invoice_konaos_no_longer_has_is_left_alone(monkeypatch):
    """Deleted in KonaOS, or never really created. Guessing an id would deep-link
    to someone else's invoice."""
    db = SessionLocal()
    try:
        _clear(db)
        _, inv = _seed(db)
        crm = _Crm([{"id": "unrelated", "eventId": "different-event",
                     "invoiceNumber": "different"}])

        result = _run(monkeypatch, crm)

        assert result["unresolved"] == 1
        assert result["filled"] == 0
        db.refresh(inv)
        assert inv.crm_invoice_id is None
    finally:
        _clear(db)
        db.close()


def test_a_stale_session_reports_rather_than_raises(monkeypatch):
    """This runs on a beat schedule; an expired key must not turn into a crashing
    task every hour."""
    db = SessionLocal()
    try:
        _clear(db)
        _seed(db)
        crm = _Crm([], fail=True)

        result = _run(monkeypatch, crm)

        assert result["filled"] == 0
        assert result["unresolved"] == 1
        assert "401" in result["error"]
    finally:
        _clear(db)
        db.close()


def test_the_invoice_number_is_the_fallback_key(monkeypatch):
    db = SessionLocal()
    try:
        _clear(db)
        _, inv = _seed(db)
        crm = _Crm([{"id": KOS_INVOICE, "eventId": None,
                     "invoiceNumber": "K530X9179333"}])

        assert _run(monkeypatch, crm)["filled"] == 1
        db.refresh(inv)
        assert inv.crm_invoice_id == KOS_INVOICE
    finally:
        _clear(db)
        db.close()
