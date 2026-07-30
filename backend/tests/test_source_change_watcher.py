"""Source-change detection: re-run an event when KonaOS moves under us.

The event behind this: ThriftBooks 2026-07-29 was processed before the driver
typed "Served 31 Konas", so it billed $0.00 and looked finished. The watcher's
whole job is that nothing stays stale silently — and its whole risk is doing so
much re-running that it either stampedes KonaOS or chases its own writes.
"""
import os
from datetime import date, datetime, timedelta, timezone

import pytest

os.environ.setdefault("CRM_PROVIDER", "mock")

from app.core.source_fingerprint import (BILLING_SOURCE_FIELDS,  # noqa: E402
                                         fingerprint)
from app.core.source_fingerprint import changed_fields  # noqa: E402
from app.db.base import Base, SessionLocal, engine  # noqa: E402
from app.models import CrmAuditEntry, Event, PipelineRun  # noqa: E402

Base.metadata.create_all(bind=engine)

# celery isn't installed in every local env; the task module needs it.
pytest.importorskip("celery")

from app.tasks import watch_tasks  # noqa: E402

THRIFTBOOKS = {
    "DATE": "2026-07-29",
    "EVENT_STARTED": "2026-07-29T17:30:00",
    "EVENT_ENDED": "2026-07-29T18:00:00",
    "ADMIN_NOTES": "$4 12oz Kona / waived $50 destination fee",
    "EVENT_NOTES_HTML": "EVENT TYPE Invoice ATTENDEES 50 associates",
    "DRIVER_NOTES": "",
    "EQUIPMENT": "KEV1 (SM)",
    "FINAL_EVENT_STATUS": "Confirmed",
    "EVENT_NAME": "ThriftBooks",
}


# ── the fingerprint itself ───────────────────────────────────────────────────

def test_the_driver_filling_in_a_serving_count_is_a_change():
    before = fingerprint(THRIFTBOOKS)
    after = fingerprint({**THRIFTBOOKS, "DRIVER_NOTES": "Served 31 Konas. Send invoice"})
    assert before != after


def test_trailing_whitespace_is_not_a_change():
    """KonaOS returns "Send invoice " with a trailing space; a re-serialization
    that drops it must not read as an edit and trigger a pointless re-run."""
    assert fingerprint({**THRIFTBOOKS, "DRIVER_NOTES": "Served 31 Konas. "}) == \
           fingerprint({**THRIFTBOOKS, "DRIVER_NOTES": "Served 31 Konas."})


def test_our_own_writeback_fields_are_not_watched():
    """The pipeline PUTs ccAmount / taxPercent / tipAmount / giveback back onto
    non-package events. If those counted as source changes, every sync would
    trigger a re-run, which would sync again — an unbounded loop against the
    client's CRM. This is the single most important property here."""
    for written_back in ("EVENT_SALES", "NET_EVENT_SALES", "BALANCE",
                         "SALES_TAX", "TIP_AMOUNT", "UPDATED_AT"):
        assert written_back not in BILLING_SOURCE_FIELDS, written_back
        assert fingerprint({**THRIFTBOOKS, written_back: 999}) == \
               fingerprint(THRIFTBOOKS), f"{written_back} must not move the hash"


def test_a_corrected_phone_number_does_not_redraft_an_invoice():
    assert fingerprint({**THRIFTBOOKS, "CONTACT_PHONE": "15551234567"}) == \
           fingerprint(THRIFTBOOKS)


def test_missing_and_empty_read_the_same():
    """An absent key and an empty string are the same fact — otherwise KonaOS
    omitting driverNotes instead of returning "" looks like an edit."""
    without = {k: v for k, v in THRIFTBOOKS.items() if k != "DRIVER_NOTES"}
    assert fingerprint(without) == fingerprint({**THRIFTBOOKS, "DRIVER_NOTES": ""})


# ── the watcher pass ─────────────────────────────────────────────────────────

def _seed(db, crm_id, *, fp=None, driver_notes="", days_ago=1, status="processed",
          final_status="Confirmed"):
    ev = Event(
        crm_event_id=crm_id,
        event_name="ThriftBooks",
        brand="Kona Ice",
        event_date=(date.today() - timedelta(days=days_ago)).isoformat(),
        final_status=final_status,
        status=status,
        status_reason="",
        source_fingerprint=fp,
        cleaned={**THRIFTBOOKS, "DRIVER_NOTES": driver_notes},
        raw={}, classification={}, square={}, calculations={},
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev


class _Crm:
    """Stands in for KonaOS: returns whatever raw payload the test wants."""

    def __init__(self, payloads):
        self.payloads = payloads
        self.calls: list[str] = []

    def get_event(self, event_id):
        self.calls.append(event_id)
        return self.payloads.get(event_id, {})


def _run(monkeypatch, crm, **kwargs):
    monkeypatch.setattr(watch_tasks.factory, "get_crm", lambda: crm)
    monkeypatch.setattr(
        watch_tasks.event_cleaner, "clean_event",
        lambda raw, brand_name="": raw.get("cleaned", {}),
    )
    dispatched = {}
    monkeypatch.setattr(
        watch_tasks, "rerun_changed_events", watch_tasks.rerun_changed_events
    )
    import app.tasks.pipeline_tasks as pt
    monkeypatch.setattr(
        pt.run_pipeline_task, "delay",
        lambda **kw: dispatched.update(kw),
    )
    result = watch_tasks.rerun_changed_events(pace_seconds=0, **kwargs)
    return result, dispatched


def _clear(db):
    db.query(Event).delete()
    db.query(PipelineRun).delete()
    db.query(CrmAuditEntry).delete()
    db.commit()


# ── what changed, in plain English ───────────────────────────────────────────

def test_changed_fields_names_the_field_in_plain_english():
    diffs = changed_fields(
        THRIFTBOOKS, {**THRIFTBOOKS, "DRIVER_NOTES": "Served 31 Konas."}
    )
    assert [d["label"] for d in diffs] == ["Driver notes"]
    assert diffs[0]["before"] == ""
    assert diffs[0]["after"] == "Served 31 Konas."


def test_changed_fields_ignores_unwatched_edits():
    assert changed_fields(THRIFTBOOKS, {**THRIFTBOOKS, "TIP_AMOUNT": 12}) == []


def test_long_notes_are_truncated_for_the_log():
    diffs = changed_fields(THRIFTBOOKS, {**THRIFTBOOKS, "ADMIN_NOTES": "x" * 5000})
    assert len(diffs[0]["after"]) <= 300


def test_a_changed_event_is_re_run(monkeypatch):
    db = SessionLocal()
    try:
        _clear(db)
        stale = {**THRIFTBOOKS, "DRIVER_NOTES": ""}
        ev = _seed(db, "kos-1", fp=fingerprint(stale))
        crm = _Crm({"kos-1": {"cleaned": {**THRIFTBOOKS,
                                          "DRIVER_NOTES": "Served 31 Konas."}}})

        result, dispatched = _run(monkeypatch, crm)

        assert result["changed"] == 1
        assert result["changed_ids"] == ["kos-1"]
        run = db.get(PipelineRun, result["run_id"])
        assert run.filter_event_ids == ["kos-1"]
        assert run.trigger == "source_change"
        assert dispatched.get("run_id") == result["run_id"]

        # NOT updated here: the pipeline records the new baseline when it stores
        # the re-processed event, so a re-run that dies is retried next pass.
        db.refresh(ev)
        assert ev.source_fingerprint == fingerprint(stale)
    finally:
        _clear(db)
        db.close()


def test_the_change_is_written_to_the_konaos_change_log(monkeypatch):
    """"How would I know this happened?" — the answer has to be a row somebody
    can find, not a figure quietly moving on the event page."""
    db = SessionLocal()
    try:
        _clear(db)
        stale = {**THRIFTBOOKS, "DRIVER_NOTES": ""}
        ev = _seed(db, "kos-log", fp=fingerprint(stale))
        crm = _Crm({"kos-log": {"cleaned": {**THRIFTBOOKS,
                                            "DRIVER_NOTES": "Served 31 Konas."}}})

        _run(monkeypatch, crm)

        entry = db.query(CrmAuditEntry).filter(
            CrmAuditEntry.crm_event_id == "kos-log").one()
        assert entry.action == "source_changed"
        assert entry.event_id == ev.id
        assert entry.event_name == "ThriftBooks"
        assert "Driver notes" in entry.summary
        assert entry.detail["fields_changed"] == ["Driver notes"]
        # Before/after is the payoff — it explains why the invoice moved.
        assert entry.detail["changes"][0]["after"] == "Served 31 Konas."
    finally:
        _clear(db)
        db.close()


def test_no_change_writes_no_change_log_row(monkeypatch):
    """A log that fills up with "nothing happened" is a log nobody reads."""
    db = SessionLocal()
    try:
        _clear(db)
        _seed(db, "kos-quiet", fp=fingerprint(THRIFTBOOKS))
        crm = _Crm({"kos-quiet": {"cleaned": dict(THRIFTBOOKS)}})

        _run(monkeypatch, crm)

        assert db.query(CrmAuditEntry).count() == 0
    finally:
        _clear(db)
        db.close()


def test_baselining_writes_no_change_log_row(monkeypatch):
    db = SessionLocal()
    try:
        _clear(db)
        _seed(db, "kos-base", fp=None)
        crm = _Crm({"kos-base": {"cleaned": dict(THRIFTBOOKS)}})

        _run(monkeypatch, crm)

        assert db.query(CrmAuditEntry).count() == 0
    finally:
        _clear(db)
        db.close()


def test_an_unchanged_event_is_left_alone(monkeypatch):
    db = SessionLocal()
    try:
        _clear(db)
        _seed(db, "kos-2", fp=fingerprint(THRIFTBOOKS))
        crm = _Crm({"kos-2": {"cleaned": dict(THRIFTBOOKS)}})

        result, dispatched = _run(monkeypatch, crm)

        assert result["changed"] == 0
        assert result["run_id"] is None
        assert dispatched == {}
    finally:
        _clear(db)
        db.close()


def test_a_pre_existing_event_is_baselined_not_re_run(monkeypatch):
    """The anti-stampede rule. Treating "no fingerprint yet" as changed would
    re-run every event in the window on the first pass after deploy — mass
    unattended writes to KonaOS is the 2026-07-21 incident."""
    db = SessionLocal()
    try:
        _clear(db)
        ev = _seed(db, "kos-3", fp=None)
        crm = _Crm({"kos-3": {"cleaned": dict(THRIFTBOOKS)}})

        result, dispatched = _run(monkeypatch, crm)

        assert result["baselined"] == 1
        assert result["changed"] == 0
        assert dispatched == {}
        db.refresh(ev)
        assert ev.source_fingerprint == fingerprint(THRIFTBOOKS)
    finally:
        _clear(db)
        db.close()


def test_cancelled_events_are_not_polled(monkeypatch):
    db = SessionLocal()
    try:
        _clear(db)
        _seed(db, "kos-4", fp=fingerprint(THRIFTBOOKS), final_status="Cancelled")
        crm = _Crm({"kos-4": {"cleaned": dict(THRIFTBOOKS)}})

        result, _ = _run(monkeypatch, crm)

        assert crm.calls == [], "a cancelled event has nothing to re-bill"
        assert result["checked"] == 0
    finally:
        _clear(db)
        db.close()


def test_events_outside_the_lookback_are_not_polled(monkeypatch):
    db = SessionLocal()
    try:
        _clear(db)
        _seed(db, "kos-5", fp=fingerprint(THRIFTBOOKS), days_ago=90)
        crm = _Crm({"kos-5": {"cleaned": dict(THRIFTBOOKS)}})

        result, _ = _run(monkeypatch, crm, lookback_days=14)

        assert crm.calls == []
        assert result["checked"] == 0
    finally:
        _clear(db)
        db.close()


def test_the_per_pass_cap_is_respected(monkeypatch):
    """KonaOS bursts have killed the session key before, so the cap is a real
    safety limit and not a performance tweak."""
    db = SessionLocal()
    try:
        _clear(db)
        for i in range(6):
            _seed(db, f"kos-cap-{i}", fp=fingerprint(THRIFTBOOKS))
        crm = _Crm({f"kos-cap-{i}": {"cleaned": dict(THRIFTBOOKS)} for i in range(6)})

        result, _ = _run(monkeypatch, crm, limit=2)

        assert len(crm.calls) == 2
        assert result["checked"] == 2
    finally:
        _clear(db)
        db.close()


def test_a_failed_fetch_does_not_end_the_pass(monkeypatch):
    db = SessionLocal()
    try:
        _clear(db)
        _seed(db, "kos-bad", fp=fingerprint(THRIFTBOOKS))
        _seed(db, "kos-good", fp=fingerprint(THRIFTBOOKS))

        class _Flaky(_Crm):
            def get_event(self, event_id):
                self.calls.append(event_id)
                if event_id == "kos-bad":
                    raise RuntimeError("KonaOS API error 500")
                return {"cleaned": {**THRIFTBOOKS, "DRIVER_NOTES": "Served 31."}}

        crm = _Flaky({})
        result, dispatched = _run(monkeypatch, crm)

        assert result["failed"] == 1
        assert result["changed"] == 1
        assert dispatched.get("run_id") == result["run_id"]
    finally:
        _clear(db)
        db.close()


def test_oldest_checked_events_are_polled_first(monkeypatch):
    """With a cap, ordering IS the coverage guarantee — without it the same rows
    get re-read every pass and the rest are never checked at all."""
    db = SessionLocal()
    try:
        _clear(db)
        recent = _seed(db, "kos-recent", fp=fingerprint(THRIFTBOOKS))
        older = _seed(db, "kos-older", fp=fingerprint(THRIFTBOOKS))
        now = datetime.now(timezone.utc)
        recent.source_checked_at = now
        older.source_checked_at = now - timedelta(hours=6)
        db.commit()

        crm = _Crm({k: {"cleaned": dict(THRIFTBOOKS)}
                    for k in ("kos-recent", "kos-older")})
        _run(monkeypatch, crm, limit=1)

        assert crm.calls == ["kos-older"]
    finally:
        _clear(db)
        db.close()
