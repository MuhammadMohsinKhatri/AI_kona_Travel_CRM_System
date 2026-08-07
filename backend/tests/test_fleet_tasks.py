"""The two push notifiers: low fuel and clock in/out.

Both reuse the Alert table cash_tasks.py already established the pattern for.
What is worth testing is not that an Alert gets created — that's one line —
it's the two decisions that keep this from becoming noise: a fuel alert that
resolves itself once the tank is back up, and a clock poll that never repeats
itself for the same shift.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_konaice.db")
os.environ["CRM_PROVIDER"] = "mock"
os.environ["SQUARE_PROVIDER"] = "mock"
os.environ["OPENAI_PROVIDER"] = "mock"
os.environ["TELEGRAM_PROVIDER"] = "mock"

import pytest  # noqa: E402

from app.config import settings  # noqa: E402
from app.db.base import Base, SessionLocal, engine  # noqa: E402
from app.integrations import samsara, square_labor  # noqa: E402
from app.models import Alert, AppSetting  # noqa: E402
from app.tasks import fleet_tasks  # noqa: E402


def setup_module(_):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    db = SessionLocal()
    db.query(Alert).delete()
    db.query(AppSetting).filter(
        AppSetting.key == fleet_tasks.CLOCK_CURSOR_KEY
    ).delete()
    db.commit()
    db.close()
    monkeypatch.setattr(settings, "samsara_api_token", "test-token")
    monkeypatch.setattr(settings, "square_kona_token", "test-token")
    monkeypatch.setattr(settings, "telegram_provider", "mock")
    yield


# ── fuel: self-resolving, never doubled ─────────────────────────────────────

def test_a_low_truck_gets_one_alert(monkeypatch):
    monkeypatch.setattr(samsara, "fuel_levels", lambda: [
        {"id": "v1", "name": "Truck 1", "percent": 12, "at": ""},
    ])
    result = fleet_tasks.check_fuel_levels()
    assert result["alerts_created"] == 1
    db = SessionLocal()
    alert = db.query(Alert).filter(Alert.source == "fuel").one()
    assert alert.resolved is False
    assert "Truck 1" in alert.issue
    db.close()


def test_running_again_while_still_low_does_not_duplicate(monkeypatch):
    """The whole point of the guard — otherwise every 6am run for a truck
    parked at a dead sensor spams the same fact forever."""
    monkeypatch.setattr(samsara, "fuel_levels", lambda: [
        {"id": "v1", "name": "Truck 1", "percent": 12, "at": ""},
    ])
    fleet_tasks.check_fuel_levels()
    result = fleet_tasks.check_fuel_levels()
    assert result["alerts_created"] == 0
    db = SessionLocal()
    assert db.query(Alert).filter(Alert.source == "fuel").count() == 1
    db.close()


def test_refuelling_resolves_the_alert_on_its_own(monkeypatch):
    """Nobody should have to remember to click 'sorted' for a truck that
    already fixed itself at the pump."""
    monkeypatch.setattr(samsara, "fuel_levels", lambda: [
        {"id": "v1", "name": "Truck 1", "percent": 12, "at": ""},
    ])
    fleet_tasks.check_fuel_levels()

    monkeypatch.setattr(samsara, "fuel_levels", lambda: [
        {"id": "v1", "name": "Truck 1", "percent": 90, "at": ""},
    ])
    result = fleet_tasks.check_fuel_levels()
    assert result["alerts_resolved"] == 1
    db = SessionLocal()
    alert = db.query(Alert).filter(Alert.source == "fuel").one()
    assert alert.resolved is True
    db.close()


def test_dropping_low_again_after_resolution_re_alerts(monkeypatch):
    monkeypatch.setattr(samsara, "fuel_levels", lambda: [
        {"id": "v1", "name": "Truck 1", "percent": 12, "at": ""},
    ])
    fleet_tasks.check_fuel_levels()
    monkeypatch.setattr(samsara, "fuel_levels", lambda: [
        {"id": "v1", "name": "Truck 1", "percent": 90, "at": ""},
    ])
    fleet_tasks.check_fuel_levels()
    monkeypatch.setattr(samsara, "fuel_levels", lambda: [
        {"id": "v1", "name": "Truck 1", "percent": 8, "at": ""},
    ])
    result = fleet_tasks.check_fuel_levels()
    assert result["alerts_created"] == 1
    db = SessionLocal()
    assert db.query(Alert).filter(
        Alert.source == "fuel", Alert.resolved.is_(False)
    ).count() == 1
    db.close()


def test_critically_low_is_higher_severity_than_merely_low(monkeypatch):
    monkeypatch.setattr(samsara, "fuel_levels", lambda: [
        {"id": "v1", "name": "Truck 1", "percent": 5, "at": ""},
        {"id": "v2", "name": "Truck 2", "percent": 20, "at": ""},
    ])
    fleet_tasks.check_fuel_levels()
    db = SessionLocal()
    sevs = {a.issue: a.severity for a in db.query(Alert).filter(Alert.source == "fuel")}
    assert sevs["Low fuel: Truck 1"] == "HIGH"
    assert sevs["Low fuel: Truck 2"] == "MEDIUM"
    db.close()


def test_unconfigured_samsara_skips_cleanly(monkeypatch):
    monkeypatch.setattr(settings, "samsara_api_token", "")
    result = fleet_tasks.check_fuel_levels()
    assert "skipped" in result
    db = SessionLocal()
    assert db.query(Alert).filter(Alert.source == "fuel").count() == 0
    db.close()


# ── clock: never notified twice for the same shift ──────────────────────────

SHIFT_OPEN = {
    "brand": "kona", "name": "Sam Driver", "clock_in": "2026-08-07T13:00:00Z",
    "clock_out": "", "open": True, "hours": None, "id": "tc-1",
}
SHIFT_CLOSED = {**SHIFT_OPEN, "clock_out": "2026-08-07T21:00:00Z",
               "open": False, "hours": 8.0}


def test_a_clock_in_notifies_once(monkeypatch):
    monkeypatch.setattr(square_labor, "timecards", lambda day=None: [dict(SHIFT_OPEN)])
    result = fleet_tasks.poll_clock_events()
    assert result["notified"] == 1
    db = SessionLocal()
    alert = db.query(Alert).filter(Alert.source == "clock").one()
    assert "clocked in" in alert.issue
    assert alert.resolved is True  # informational, not something to act on
    db.close()


def test_polling_again_with_no_change_notifies_nothing(monkeypatch):
    """The core guarantee: a 20-minute poll must not re-announce the same
    clock-in every time it runs."""
    monkeypatch.setattr(square_labor, "timecards", lambda day=None: [dict(SHIFT_OPEN)])
    fleet_tasks.poll_clock_events()
    result = fleet_tasks.poll_clock_events()
    assert result["notified"] == 0


def test_the_same_shift_closing_notifies_again_for_clock_out(monkeypatch):
    """Clock-in and clock-out are two separate facts about the same timecard
    id — closing a shift must notify even though the id was already seen."""
    monkeypatch.setattr(square_labor, "timecards", lambda day=None: [dict(SHIFT_OPEN)])
    fleet_tasks.poll_clock_events()  # the clock-in, already covered above

    monkeypatch.setattr(square_labor, "timecards", lambda day=None: [dict(SHIFT_CLOSED)])
    result = fleet_tasks.poll_clock_events()
    assert result["notified"] == 1
    db = SessionLocal()
    alert = db.query(Alert).filter(
        Alert.source == "clock", Alert.issue.like("%clocked out%")
    ).one()
    assert "8.0" in alert.issue or "8h" in alert.issue.replace(".0h", "h")
    db.close()


def test_unconfigured_square_skips_cleanly(monkeypatch):
    monkeypatch.setattr(settings, "square_kona_token", "")
    monkeypatch.setattr(settings, "square_tom_token", "")
    result = fleet_tasks.poll_clock_events()
    assert "skipped" in result


def test_a_flood_of_clock_events_is_capped_not_broadcast(monkeypatch):
    """The circuit breaker. A wrong Square filter key returns 200 OK with the
    whole account's history, and every row looks like a new clock-in — so the
    failure mode is hundreds of Telegram messages about shifts from years ago,
    not an error anyone would see. Cap the pass and log loudly instead."""
    flood = [
        {**SHIFT_CLOSED, "id": f"tc-{i}", "name": f"Person {i}"}
        for i in range(40)
    ]
    monkeypatch.setattr(square_labor, "timecards", lambda day=None: flood)
    result = fleet_tasks.poll_clock_events()

    assert result["notified"] == fleet_tasks.MAX_NOTIFY_PER_PASS
    assert result["suppressed"] > 0
    db = SessionLocal()
    assert db.query(Alert).filter(Alert.source == "clock").count() == \
        fleet_tasks.MAX_NOTIFY_PER_PASS
    db.close()


def test_suppressed_events_are_not_re_sent_on_the_next_pass(monkeypatch):
    """Ids are recorded even when the notification is dropped, so a flood is
    swallowed once rather than re-delivered every 20 minutes forever."""
    flood = [
        {**SHIFT_CLOSED, "id": f"tc-{i}", "name": f"Person {i}"}
        for i in range(40)
    ]
    monkeypatch.setattr(square_labor, "timecards", lambda day=None: flood)
    fleet_tasks.poll_clock_events()
    second = fleet_tasks.poll_clock_events()
    assert second["notified"] == 0
    assert second["suppressed"] == 0


# ── the DAILY REPORT, not just the alert ────────────────────────────────────

def test_the_report_goes_out_even_when_every_truck_is_fine(monkeypatch):
    """The difference between an alert and a report. A quiet phone is
    ambiguous — either every truck is full or the job died three weeks ago —
    and not having to wonder which is the whole point of a daily check."""
    sent = []
    monkeypatch.setattr(samsara, "fuel_levels", lambda: [
        {"id": "v1", "name": "Truck 1", "percent": 90, "at": ""},
    ])
    monkeypatch.setattr(fleet_tasks.notify, "send_message",
                        lambda db, text: sent.append(text) or {"sent": 2})
    result = fleet_tasks.check_fuel_levels()

    assert result["alerts_created"] == 0      # nothing wrong
    assert result["report_sent"] == 2         # ...and it still reported
    assert "All trucks above" in sent[0]


def test_the_report_lists_every_truck_worst_first(monkeypatch):
    sent = []
    monkeypatch.setattr(samsara, "fuel_levels", lambda: [
        {"id": "v1", "name": "Full One", "percent": 86, "at": ""},
        {"id": "v2", "name": "Empty One", "percent": 6, "at": ""},
        {"id": "v3", "name": "Low One", "percent": 15, "at": ""},
    ])
    monkeypatch.setattr(fleet_tasks.notify, "send_message",
                        lambda db, text: sent.append(text) or {"sent": 1})
    fleet_tasks.check_fuel_levels()

    body = sent[0]
    assert body.index("Empty One") < body.index("Low One") < body.index("Full One")
    assert "2 trucks below" in body


def test_a_low_truck_is_not_messaged_twice(monkeypatch):
    """One digest carries every truck, so a per-alert push as well would say
    the same thing about the same truck in two different messages."""
    sent = []
    monkeypatch.setattr(samsara, "fuel_levels", lambda: [
        {"id": "v1", "name": "Truck 1", "percent": 12, "at": ""},
    ])
    monkeypatch.setattr(fleet_tasks.notify, "send_message",
                        lambda db, text: sent.append(text) or {"sent": 1})
    def fail(*a, **k):
        raise AssertionError("notify_alert must not push — the digest covers it")
    monkeypatch.setattr(fleet_tasks.notify, "notify_alert", fail)

    result = fleet_tasks.check_fuel_levels()
    assert result["alerts_created"] == 1     # still on Needs Attention
    assert len(sent) == 1                    # ...but exactly one message


def test_a_truck_with_no_reading_is_shown_as_unknown_not_empty(monkeypatch):
    sent = []
    monkeypatch.setattr(samsara, "fuel_levels", lambda: [
        {"id": "v1", "name": "No Sender", "percent": None, "at": ""},
        {"id": "v2", "name": "Fine", "percent": 70, "at": ""},
    ])
    monkeypatch.setattr(fleet_tasks.notify, "send_message",
                        lambda db, text: sent.append(text) or {"sent": 1})
    fleet_tasks.check_fuel_levels()

    assert "No Sender — no reading" in sent[0]
    assert "All trucks above" in sent[0]   # unknown is not counted as low


def test_a_truck_name_cannot_break_telegrams_html(monkeypatch):
    """An unescaped & or < makes Telegram reject the whole message, so one
    oddly-named truck would silence the entire morning report."""
    sent = []
    monkeypatch.setattr(samsara, "fuel_levels", lambda: [
        {"id": "v1", "name": "Kona & Tom's <Spare>", "percent": 50, "at": ""},
    ])
    monkeypatch.setattr(fleet_tasks.notify, "send_message",
                        lambda db, text: sent.append(text) or {"sent": 1})
    fleet_tasks.check_fuel_levels()

    assert "&amp;" in sent[0] and "&lt;Spare&gt;" in sent[0]
