"""Cash provenance on the ledger: who set this figure?

A bare "$7.00" in the Cash Collected column doesn't say whether somebody counted
the till, a bot posted it, or the classifier guessed it from the driver's notes —
and those carry very different weight when a number looks wrong. The dot marks
that SOMETHING set it; this is the part that names who.

The audit rows already existed (financials._audit_cash writes them on every cash
update). What was missing was getting them onto the ledger row so the column can
answer the question where it's asked.
"""
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("CRM_PROVIDER", "mock")

from app.api.routes.financials import _CASH_HISTORY_LIMIT, _cash_history
from app.db.base import Base, SessionLocal, engine
from app.models import CrmAuditEntry

Base.metadata.create_all(bind=engine)


def _entry(db, crm_id, *, amount, previous, by, source="api", minutes_ago=0,
           action="cash_updated"):
    row = CrmAuditEntry(
        crm_event_id=crm_id,
        event_name="(IC) Pikesville Farmers Market",
        event_date="2026-07-28",
        action=action,
        summary=f"Cash set to ${amount:,.2f} (was ${previous:,.2f}) by {by}",
        detail={
            "source": source, "by": by, "previous": previous,
            "values": {"cash_collected": amount},
        },
    )
    db.add(row)
    db.flush()
    # created_at is a server default, so it has to be set after the flush to
    # control ordering in these tests.
    row.created_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    db.commit()
    return row


def _clear(db):
    db.query(CrmAuditEntry).delete()
    db.commit()


def test_the_history_names_who_posted_the_figure():
    db = SessionLocal()
    try:
        _clear(db)
        _entry(db, "kos-cash", amount=7.0, previous=0.0, by="Linda")

        hist = _cash_history(db, ["kos-cash"])["kos-cash"]

        assert len(hist) == 1
        assert hist[0]["by"] == "Linda"
        assert hist[0]["amount"] == 7.0
        assert hist[0]["previous"] == 0.0
        assert hist[0]["source"] == "api"
        assert hist[0]["at"]
    finally:
        _clear(db)
        db.close()


def test_newest_first():
    """The current figure's origin is the one being asked about, so it leads."""
    db = SessionLocal()
    try:
        _clear(db)
        _entry(db, "kos-cash", amount=5.0, previous=0.0, by="Linda", minutes_ago=90)
        _entry(db, "kos-cash", amount=7.0, previous=5.0, by="Mohsin", minutes_ago=5)

        hist = _cash_history(db, ["kos-cash"])["kos-cash"]

        assert [h["by"] for h in hist] == ["Mohsin", "Linda"]
    finally:
        _clear(db)
        db.close()


def test_only_cash_updates_are_included():
    """The audit table holds invoice and event writes too; a cash tooltip that
    listed those would be noise dressed as provenance."""
    db = SessionLocal()
    try:
        _clear(db)
        _entry(db, "kos-cash", amount=7.0, previous=0.0, by="Linda")
        _entry(db, "kos-cash", amount=0.0, previous=0.0, by="pipeline",
               action="event_updated")
        _entry(db, "kos-cash", amount=0.0, previous=0.0, by="pipeline",
               action="source_changed")

        assert len(_cash_history(db, ["kos-cash"])["kos-cash"]) == 1
    finally:
        _clear(db)
        db.close()


def test_history_is_capped():
    """Deep history is the change log's job. The tooltip stays readable."""
    db = SessionLocal()
    try:
        _clear(db)
        for i in range(_CASH_HISTORY_LIMIT + 4):
            _entry(db, "kos-cash", amount=float(i), previous=0.0,
                   by=f"user{i}", minutes_ago=i)

        assert len(_cash_history(db, ["kos-cash"])["kos-cash"]) == _CASH_HISTORY_LIMIT
    finally:
        _clear(db)
        db.close()


def test_rows_are_keyed_per_event_in_one_query():
    """The ledger renders a month of events at a time, so this must not be an
    N+1 — one call covers every row on the page, split by event."""
    db = SessionLocal()
    try:
        _clear(db)
        _entry(db, "kos-a", amount=7.0, previous=0.0, by="Linda")
        _entry(db, "kos-b", amount=12.0, previous=0.0, by="the Square bot")

        hist = _cash_history(db, ["kos-a", "kos-b", "kos-untouched"])

        assert hist["kos-a"][0]["by"] == "Linda"
        assert hist["kos-b"][0]["by"] == "the Square bot"
        assert "kos-untouched" not in hist, "no log means no key, so the UI shows none"
    finally:
        _clear(db)
        db.close()


def test_no_events_means_no_query():
    db = SessionLocal()
    try:
        assert _cash_history(db, []) == {}
    finally:
        db.close()


def test_a_caller_that_did_not_say_who_leaves_by_empty():
    """"" is honest and lets the UI fall back to the source ("an automation").
    Inventing a name here would put a fiction into an audit trail."""
    db = SessionLocal()
    try:
        _clear(db)
        _entry(db, "kos-cash", amount=7.0, previous=0.0, by="")

        assert _cash_history(db, ["kos-cash"])["kos-cash"][0]["by"] == ""
    finally:
        _clear(db)
        db.close()
