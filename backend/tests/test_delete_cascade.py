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
