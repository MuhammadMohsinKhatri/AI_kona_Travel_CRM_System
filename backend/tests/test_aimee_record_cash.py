"""record_cash: picking one event out of several that share a name.

Five events are called "(IC) Pikesville Farmers Market" and differ only by
date. Refusing to guess between them is right — but the refusal was a dead end,
because the tool took only a name and an amount. Told to "say which", the model
did the one thing available and folded the date into the name:

    event="(IC) Pikesville Farmers Market (2026-07-28)"

which matches nothing, and reads to the user as the system losing an event it
had listed a moment earlier. The guard was sound; there was no way to answer it.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_konaice.db")
os.environ["CRM_PROVIDER"] = "mock"
os.environ["SQUARE_PROVIDER"] = "mock"
os.environ["OPENAI_PROVIDER"] = "mock"
os.environ["TELEGRAM_PROVIDER"] = "mock"

import pytest  # noqa: E402

from app.aimee.tools.finance import _find_event, record_cash  # noqa: E402
from app.db.base import Base, SessionLocal, engine  # noqa: E402
from app.models import Event, FinancialEntry  # noqa: E402

NAME = "(IC) Pikesville Farmers Market"
DATES = ["2026-08-04", "2026-07-28", "2026-07-21", "2026-07-14", "2026-07-07"]


def setup_module(_):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def _events():
    db = SessionLocal()
    db.query(FinancialEntry).delete()
    db.query(Event).delete()
    # financial_entries.event_id is NOT NULL, so the events have to exist
    # before their ledger rows can point at them.
    rows = [(f"K530X{i}", NAME, d) for i, d in enumerate(DATES)]
    rows.append(("K999", "Arbutus Senior Center", "2026-07-27"))
    for crm_id, name, d in rows:
        ev = Event(crm_event_id=crm_id, event_name=name, event_date=d,
                   brand="Kona Ice", status="processed")
        db.add(ev)
        db.flush()
        db.add(FinancialEntry(event_id=ev.id, crm_event_id=crm_id,
                              event_name=name, event_date=d, cash_collected=0.0))
    db.commit()
    yield db
    db.close()


def test_a_shared_name_alone_still_refuses(_events):
    """The guard that was already right — five candidates, no guess."""
    r = record_cash(db=_events, event=NAME, amount=7)
    assert r.ok is False and r.proposal is None
    assert "matches 5 events" in r.error
    assert "event_date" in r.error, "the refusal must name how to answer it"


def test_the_date_parameter_resolves_it(_events):
    """The missing half: a way to answer 'say which'."""
    r = record_cash(db=_events, event=NAME, amount=7, event_date="2026-07-28")
    assert r.ok is True
    assert r.proposal["event_date"] == "2026-07-28"
    assert r.proposal["new_cash"] == 7.0


def test_a_date_folded_into_the_name_still_works(_events):
    """The exact call that failed in production. A model shown
    "Name (2026-07-28)" in an error will sometimes send it back that way
    however the schema is worded, so the name is parsed rather than trusted."""
    r = record_cash(db=_events, event=f"{NAME} (2026-07-28)", amount=7)
    assert r.ok is True, r.error
    assert r.proposal["event_date"] == "2026-07-28"


def test_a_real_name_with_a_wrong_date_lists_the_real_dates(_events):
    """"No event matches" is a bad answer when the name plainly exists — it
    was the DATE that narrowed it to nothing, so say so and show the options."""
    r = record_cash(db=_events, event=NAME, amount=7, event_date="2026-01-01")
    assert r.ok is False and r.proposal is None
    assert "2026-07-28" in r.error
    assert "2026-01-01" in r.error


def test_an_unambiguous_name_needs_no_date(_events):
    r = record_cash(db=_events, event="Arbutus", amount=63)
    assert r.ok is True
    assert r.proposal["event_name"] == "Arbutus Senior Center"


def test_find_event_strips_the_date_before_matching_the_name(_events):
    found = _find_event(_events, f"{NAME} (2026-07-21)")
    assert found is not None and found.event_date == "2026-07-21"
