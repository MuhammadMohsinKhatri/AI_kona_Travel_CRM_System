"""Row deletes: an event takes its children with it, and nothing else leaks.

The ledger cascade is declared on the ORM relationship rather than left to the
DB's ondelete=CASCADE — Postgres enforces the FK but SQLite only does with
PRAGMA foreign_keys=ON, so an FK-only cascade orphans the row here while
passing in production.
"""
import os

# Importing app.models pulls in app.db.base, which builds the engine at import
# time from settings (i.e. .env → Postgres). This module sorts before
# test_pipeline, so it would win that race and point the whole session at
# production's DB URL. Pin SQLite first, exactly as test_pipeline does.
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_konaice.db")
os.environ.setdefault("CRM_PROVIDER", "mock")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models import Alert, Event, FinancialEntry, Invoice, PipelineRun


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")  # in-memory, fresh per test
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _event_with_children(db) -> Event:
    e = Event(crm_event_id="X1", event_name="Test", status="processed")
    db.add(e)
    db.commit()
    db.add_all([
        Invoice(event_id=e.id, grand_total=100.0),
        Alert(event_id=e.id, severity="HIGH", issue="test"),
        FinancialEntry(event_id=e.id, crm_event_id="X1"),
    ])
    db.commit()
    return e


def _counts(db):
    return (db.query(Event).count(), db.query(Invoice).count(),
            db.query(Alert).count(), db.query(FinancialEntry).count())


def test_deleting_event_cascades_to_invoice_alert_and_ledger(db):
    e = _event_with_children(db)
    assert _counts(db) == (1, 1, 1, 1)
    db.delete(e)
    db.commit()
    assert _counts(db) == (0, 0, 0, 0), "event delete must leave no orphans"


def test_deleting_ledger_row_keeps_its_event(db):
    e = _event_with_children(db)
    db.delete(db.query(FinancialEntry).one())
    db.commit()
    # The event survives, so re-running the pipeline can rebuild the row.
    assert db.query(Event).count() == 1
    assert db.query(FinancialEntry).count() == 0


def test_bulk_delete_filters_by_date_range_and_cascades(db):
    from app.api.routes.events import _filtered_events

    for i, date in enumerate(["2026-07-10", "2026-07-12", "2026-07-15"], 1):
        e = Event(crm_event_id=f"D{i}", event_name=f"E{i}", event_date=date,
                  status="processed")
        db.add(e)
        db.commit()
        db.add(Invoice(event_id=e.id, grand_total=50.0))
        db.commit()

    # Same deletion path as the endpoint: ORM deletes so cascades run.
    matched = _filtered_events(db, date_from="2026-07-11", date_to="2026-07-14").all()
    assert [e.event_date for e in matched] == ["2026-07-12"]
    for e in matched:
        db.delete(e)
    db.commit()

    remaining = {e.event_date for e in db.query(Event).all()}
    assert remaining == {"2026-07-10", "2026-07-15"}
    assert db.query(Invoice).count() == 2, "matched event's invoice must cascade"


def test_bulk_delete_endpoint_refuses_unfiltered_wipe(db):
    from fastapi import HTTPException

    from app.api.routes.events import delete_events_bulk

    db.add(Event(crm_event_id="K1", event_name="Keep"))
    db.commit()
    with pytest.raises(HTTPException) as exc:
        delete_events_bulk(db=db, _=None)
    assert exc.value.status_code == 400
    assert db.query(Event).count() == 1, "nothing may be deleted without a filter"


def test_financials_bulk_delete_is_date_scoped(db):
    from fastapi import HTTPException

    from app.api.routes.financials import delete_entries_bulk

    # One ledger row per event (event_id is unique) → two events, two rows.
    for i, date in enumerate(["2026-07-08", "2026-07-09"], 1):
        e = Event(crm_event_id=f"F{i}", event_name=f"Keep-{i}", status="processed")
        db.add(e)
        db.commit()
        db.add(FinancialEntry(event_id=e.id, crm_event_id=f"F{i}",
                              event_date=date, month="2026-07"))
        db.commit()

    # No date scope → 400, nothing deleted (brand alone isn't enough).
    with pytest.raises(HTTPException) as exc:
        delete_entries_bulk(db=db, _=None, brand="Kona Ice")
    assert exc.value.status_code == 400
    assert db.query(FinancialEntry).count() == 2

    # Single-day scope deletes only that day's rows; the event survives.
    out = delete_entries_bulk(db=db, _=None, from_date="2026-07-08", to_date="2026-07-08")
    assert out == {"deleted": 1}
    remaining = db.query(FinancialEntry).all()
    assert [r.event_date for r in remaining] == ["2026-07-09"]
    assert db.query(Event).count() == 2, "ledger bulk delete must not touch events"

    # Month scope wipes the rest of the month.
    out = delete_entries_bulk(db=db, _=None, month="2026-07")
    assert out == {"deleted": 1}
    assert db.query(FinancialEntry).count() == 0


def test_trigger_run_refuses_concurrent_run_for_same_date(db, monkeypatch):
    """Re-running a finished date is allowed (writes upsert); two runs over
    the same date at the same time are refused with 409."""
    from fastapi import BackgroundTasks, HTTPException

    from app.api.routes.pipeline import RunRequest, trigger_run
    from app.config import settings

    # Inline mode → BackgroundTasks records the task without executing it,
    # and the Celery import path (not installed locally) is never touched.
    monkeypatch.setattr(settings, "pipeline_run_inline", True)

    db.add(PipelineRun(status="running", trigger="scheduled", target_date="2026-07-08"))
    db.add(PipelineRun(status="completed", trigger="manual", target_date="2026-07-10"))
    db.commit()

    # Same date, still running → refused.
    with pytest.raises(HTTPException) as exc:
        trigger_run(BackgroundTasks(), body=RunRequest(target_date="2026-07-08"), db=db, _=None)
    assert exc.value.status_code == 409

    # Different date → allowed.
    assert trigger_run(BackgroundTasks(), body=RunRequest(target_date="2026-07-09"), db=db, _=None).run_id
    # Same date as a COMPLETED run → allowed (safe re-run).
    assert trigger_run(BackgroundTasks(), body=RunRequest(target_date="2026-07-10"), db=db, _=None).run_id


class _FakeCRM:
    """Records invoice calls — enough to exercise _replace_draft's routing."""

    def __init__(self, invoices):
        self.invoices = invoices
        self.deleted: list[str] = []
        self.created: list[dict] = []

    def list_invoices(self):
        return self.invoices

    def delete_invoice(self, inv_id):
        self.deleted.append(inv_id)

    def create_invoice(self, payload):
        self.created.append(payload)
        return {"invoiceId": "NEW-1"}


def test_replace_draft_protects_any_matching_invoice(db):
    """MAXIMALLY CONSERVATIVE as of the 2026-07-21 incident: any invoice
    matching the event — including one whose status parses as 'draft' — is
    left untouched. Two invoices KonaOS confirmed as genuinely sent/paid
    vanished after a pipeline re-run, so trusting the parsed status alone is
    no longer good enough; only a true zero-match event gets a new invoice."""
    from app.core.pipeline import _replace_draft

    e = Event(crm_event_id="INV-EVT-1", event_name="T", status="processed")
    db.add(e)
    db.commit()
    payload = {"invoiceNumber": "K123", "grandTotal": 100.0}

    # Paid invoice → skipped, nothing deleted, nothing created.
    crm = _FakeCRM([{"eventId": "INV-EVT-1", "invoiceId": "OLD-1", "invoiceStatus": "Paid"}])
    out = _replace_draft(db, crm, e, payload, {})
    assert out.startswith("skipped")
    assert crm.deleted == [] and crm.created == []

    # Unknown status → fail-safe skip.
    crm = _FakeCRM([{"eventId": "INV-EVT-1", "invoiceId": "OLD-2", "invoiceStatus": "weird"}])
    assert _replace_draft(db, crm, e, payload, {}).startswith("skipped")
    assert crm.deleted == []

    # Draft status → ALSO skipped now (not replaced) — nothing is ever
    # deleted by this function until the matching bug is confirmed fixed.
    crm = _FakeCRM([{"eventId": "INV-EVT-1", "invoiceId": "OLD-3", "invoiceStatus": "draft"}])
    assert _replace_draft(db, crm, e, payload, {}).startswith("skipped")
    assert crm.deleted == [] and crm.created == []

    # No match at all → the only case that creates a new invoice.
    crm = _FakeCRM([])
    assert _replace_draft(db, crm, e, payload, {}) == "created"
    assert crm.deleted == [] and len(crm.created) == 1


def test_konaos_json_body_never_contains_nan():
    """json.dumps emits literal NaN (invalid JSON, KonaOS rejects it) — the
    serializer must null them out like JSON.stringify does."""
    pytest.importorskip("httpx")
    from app.konaos.client import _js_safe
    import json as _json

    body = {"a": float("nan"), "b": [1.0, float("inf")], "c": {"d": float("-inf"), "ok": 2}}
    s = _json.dumps(_js_safe(body), separators=(",", ":"))
    assert "NaN" not in s and "Infinity" not in s
    assert s == '{"a":null,"b":[1.0,null],"c":{"d":null,"ok":2}}'


def test_list_events_filters_by_run_id(db):
    """The Runs page's per-run breakdown scopes events by run_id — including
    skipped and errored ones, not just processed — so a run's failures and
    skips are visible in one place."""
    from app.api.routes.events import list_events

    run_a = PipelineRun(status="completed", trigger="manual")
    run_b = PipelineRun(status="completed", trigger="manual")
    db.add_all([run_a, run_b])
    db.commit()
    db.add_all([
        Event(crm_event_id="A1", event_name="proc", status="processed", run_id=run_a.id),
        Event(crm_event_id="A2", event_name="skip", status="skipped",
              status_reason="cancelled", run_id=run_a.id),
        Event(crm_event_id="A3", event_name="err", status="error",
              error="KonaOS API error 500", run_id=run_a.id),
        Event(crm_event_id="B1", event_name="other", status="processed", run_id=run_b.id),
    ])
    db.commit()

    page = list_events(db=db, _=None, page=1, page_size=200, run_id=run_a.id)
    ids = {e.crm_event_id for e in page.items}
    assert ids == {"A1", "A2", "A3"}  # run_a only, all statuses
    assert page.total == 3
    # the errored row still carries its failure text for the breakdown
    err = next(e for e in page.items if e.crm_event_id == "A3")
    assert err.error == "KonaOS API error 500"


def test_deleting_run_does_not_touch_events(db):
    run = PipelineRun(status="completed", trigger="manual")
    db.add(run)
    db.commit()
    e = Event(crm_event_id="X9", event_name="T", run_id=run.id)
    db.add(e)
    db.commit()
    db.delete(run)
    db.commit()
    assert db.query(Event).count() == 1  # run_id is a plain column, no FK


def test_update_event_strips_readonly_invoice_status():
    """invoiceStatus is read-only on the KonaOS event PUT: the GET returns it
    (derived from the invoice) but the PUT DTO rejects ANY value — even null —
    with main.invalidJsonError. The read-modify-write must drop it, whether it
    arrives echoed from the GET or passed explicitly by a caller."""
    import asyncio
    from unittest.mock import patch

    from app.konaos.client import KonaosClient

    async def scenario():
        kc = KonaosClient()
        kc.session_key = "test"
        fetched = {"id": "E1", "name": "Ev", "businessName": "",
                   "invoiceStatus": None, "eventStaffList": None, "clientId": None}
        sent = {}

        async def fake_request(method, endpoint, **kw):
            class R:
                status_code = 200
                text = "{}"
                def json(self):
                    return fetched if method == "GET" else {"general": []}
                def raise_for_status(self):
                    pass
            if method != "GET":
                sent.update(kw.get("json") or {})
            return R()

        with patch.object(kc, "_make_request", side_effect=fake_request):
            await kc.update_event("E1", invoiceAmount=275.0, invoiceStatus="draft")
        await kc.close()
        return sent

    sent = asyncio.run(scenario())
    assert "invoiceStatus" not in sent
    assert sent["invoiceAmount"] == 275.0
    assert sent["businessName"] == "Ev"  # required-field fallback still applies
