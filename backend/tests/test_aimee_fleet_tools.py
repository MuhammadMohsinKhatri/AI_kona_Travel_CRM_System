"""The fleet, maps and labor tools — and the media signing that protects the
Google Maps key.

Nothing here calls Samsara, Google or Square. What is worth testing is the
judgement between the API and the answer: which truck a person meant, what
"unknown fuel" means next to "empty", which end of a journey was asked about,
and whether a signed image URL can be forged.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_konaice.db")
os.environ["CRM_PROVIDER"] = "mock"
os.environ["SQUARE_PROVIDER"] = "mock"
os.environ["OPENAI_PROVIDER"] = "mock"
os.environ["TELEGRAM_PROVIDER"] = "mock"

import json  # noqa: E402
import time  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402

from app.aimee.tools import fleet, maps  # noqa: E402
from app.api.routes import media  # noqa: E402
from app.config import settings  # noqa: E402
from app.integrations import gmaps, samsara  # noqa: E402

FLEET = [
    {"id": "v1", "name": "Kona Ice Truck 1"},
    {"id": "v2", "name": "Kona Ice Truck 2"},
    {"id": "v3", "name": "Travelin Toms Coffee"},
]


@pytest.fixture(autouse=True)
def _roster(monkeypatch):
    monkeypatch.setattr(samsara, "_roster", list(FLEET))
    monkeypatch.setattr(samsara, "vehicles", lambda refresh=False: list(FLEET))


# ── which truck did they mean ───────────────────────────────────────────────

def test_finds_a_truck_by_id_and_by_exact_name():
    assert samsara.find_vehicle("v2")["id"] == "v2"
    assert samsara.find_vehicle("Travelin Toms Coffee")["id"] == "v3"


def test_a_unique_substring_is_enough():
    assert samsara.find_vehicle("coffee")["id"] == "v3"


def test_an_ambiguous_name_returns_nothing_rather_than_the_first():
    """"the Kona truck" with two of them is a question to ask back, not a coin
    to flip — picking one would send a driver to the wrong place."""
    assert samsara.find_vehicle("Kona Ice") is None


def test_naming_an_unknown_truck_lists_the_real_ones(monkeypatch):
    monkeypatch.setattr(samsara, "locations", lambda: [])
    r = fleet.get_truck_location(db=None, truck="Ford Transit")
    assert r.ok is False
    assert "Travelin Toms Coffee" in r.error


# ── fuel: unknown is not empty ──────────────────────────────────────────────

def test_a_truck_with_no_fuel_sender_is_not_reported_as_low(monkeypatch):
    """`None` and 0 prompt opposite actions — one is "send someone to look",
    the other is "send it to a pump". Counting unknown as low would have
    somebody drive out to a truck that is perfectly full."""
    monkeypatch.setattr(samsara, "fuel_levels", lambda: [
        {"id": "v1", "name": "Kona Ice Truck 1", "percent": 12, "at": ""},
        {"id": "v2", "name": "Kona Ice Truck 2", "percent": None, "at": ""},
        {"id": "v3", "name": "Travelin Toms Coffee", "percent": 80, "at": ""},
    ])
    r = fleet.get_truck_fuel(db=None)
    assert r.ok is True
    assert r.data["low_fuel"] == ["Kona Ice Truck 1"]
    assert r.data["no_reading"] == ["Kona Ice Truck 2"]


def test_samsara_being_down_is_an_answer_not_an_exception(monkeypatch):
    """The registry contract: a dead integration becomes "I can't see the
    trucks", never a broken conversation."""
    def boom():
        raise samsara.SamsaraError("Samsara rejected the API token")
    monkeypatch.setattr(samsara, "locations", boom)
    r = fleet.get_truck_location(db=None)
    assert r.ok is False and r.retryable is True
    assert "token" in r.error


# ── one tool, both ends of the journey ──────────────────────────────────────

LEG = {
    "origin": "28 Alco Place, Lansdowne, MD",
    "destination": "Catonsville Elementary",
    "distance_text": "9.4 mi",
    "duration_seconds": 1800,
    "duration_text": "30 mins",
    "traffic_aware": True,
    "summary": "I-695",
}


def test_arrive_by_answers_when_to_leave(monkeypatch):
    monkeypatch.setattr(gmaps, "directions", lambda *a, **k: dict(LEG))
    want = datetime(2026, 8, 10, 14, 0, tzinfo=timezone.utc)
    r = maps.get_travel_time(db=None, destination="Catonsville Elementary",
                             arrive_by=want.isoformat())
    assert r.ok is True
    assert r.data["answers"] == "when to leave"
    assert r.data["leave_at"] == (want - timedelta(minutes=30)).isoformat()


def test_depart_at_answers_when_you_arrive(monkeypatch):
    monkeypatch.setattr(gmaps, "directions", lambda *a, **k: dict(LEG))
    leave = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
    r = maps.get_travel_time(db=None, destination="Catonsville Elementary",
                             depart_at=leave.isoformat())
    assert r.data["answers"] == "when you arrive"
    assert r.data["arrive_at"] == (leave + timedelta(minutes=30)).isoformat()


def test_no_origin_starts_from_the_yard(monkeypatch):
    seen = {}

    def spy(origin, destination, depart_at=None):
        seen["origin"] = origin
        return dict(LEG)

    monkeypatch.setattr(gmaps, "directions", spy)
    maps.get_travel_time(db=None, destination="anywhere")
    assert seen["origin"] == settings.fleet_home_address


# ── signed media: the key never reaches the browser ─────────────────────────

def test_a_signed_url_carries_no_api_key(monkeypatch):
    monkeypatch.setattr(settings, "google_maps_api_key", "SECRET-KEY-VALUE")
    url = media.sign_media("street_view", "28 Alco Place")
    assert "SECRET-KEY-VALUE" not in url
    assert "maps.googleapis.com" not in url
    assert url.startswith("/api/aimee/media/street_view")


def test_a_tampered_subject_fails_the_signature():
    """Without this the endpoint is an open proxy: change the address in the
    URL and Google bills us for every lookup somebody cares to loop through."""
    expires = int(time.time()) + 600
    good = media._sign("street_view", "28 Alco Place", expires)
    assert media._sign("street_view", "1600 Pennsylvania Ave", expires) != good


def test_the_signature_covers_the_expiry_too():
    """Otherwise a valid signature could be replayed forever by editing the
    expiry, and a leaked link would never stop working."""
    sig = media._sign("street_view", "28 Alco Place", 1000)
    assert media._sign("street_view", "28 Alco Place", 999999999) != sig


# ── the model never gets a URL ──────────────────────────────────────────────

def test_street_view_hides_the_url_from_the_model(monkeypatch):
    """Handed a Street View link, the model rewrote it into an invented
    maps.googleapis.com URL carrying `key=YOUR_API_KEY` and printed it as raw
    markdown. Anything a model is given, it will paraphrase — so the URL goes
    to the UI only, and the model is told the picture is already on screen."""
    monkeypatch.setattr(settings, "google_maps_api_key", "test-key")
    monkeypatch.setattr(
        gmaps, "geocode",
        lambda a: {"address": "28 Alco Pl, Halethorpe, MD 21227",
                   "latitude": 39.2, "longitude": -76.6},
    )
    r = maps.get_street_view(db=None, location="28 Alco Place")
    assert r.ok is True

    seen_by_model = json.dumps(r.for_model())
    assert "http" not in seen_by_model, "the model must not be handed a URL"
    assert "googleapis" not in seen_by_model
    assert "/api/aimee/media" not in seen_by_model

    # ...while the UI still gets exactly one, pointing at our own proxy.
    assert r.display and r.display["kind"] == "image"
    assert r.display["url"].startswith("/api/aimee/media/street_view")
    assert "test-key" not in r.display["url"]


def test_the_display_block_reaches_the_stored_record(monkeypatch):
    """for_record() is what the chat reads back; losing _display here would
    show the step line with no photo under it."""
    monkeypatch.setattr(settings, "google_maps_api_key", "test-key")
    monkeypatch.setattr(
        gmaps, "geocode",
        lambda a: {"address": "28 Alco Pl", "latitude": 39.2, "longitude": -76.6},
    )
    rec = maps.get_street_view(db=None, location="28 Alco Place").for_record()
    assert rec["_display"]["kind"] == "image"
    assert rec["ok"] is True


# ── a failed write must not look like a staged one ──────────────────────────

def test_a_failed_write_says_nothing_was_staged():
    """The bug this exists for: record_cash correctly refused an ambiguous
    event name, and the model replied "I'll record $7 for the 2026-07-28 one.
    Confirm below." Nothing was below — no proposal meant no card. A bare
    error string leaves room to invent a confirmation step, so the payload
    now closes it."""
    from app.aimee.registry import ToolResult

    failed = ToolResult(ok=False, error='"Pikesville" matches 5 events — say which')
    payload = failed.for_model("write")

    assert payload["ok"] is False
    assert payload["no_change_staged"] is True
    assert "confirm below" in payload["next_step"].lower()
    # and it must not carry the success marker that licenses that phrase
    assert "awaiting_confirmation" not in payload


def test_a_failed_read_is_not_cluttered_with_write_wording():
    """Reads stage nothing by definition — telling the model so on every
    failed lookup is noise that dilutes the instruction where it matters."""
    from app.aimee.registry import ToolResult

    payload = ToolResult(ok=False, error="Samsara is down").for_model("read")
    assert "no_change_staged" not in payload
    assert "next_step" not in payload


def test_a_successful_write_still_says_awaiting_confirmation():
    """The other half of the contract — a real proposal must keep licensing
    the 'confirm below' phrasing, or the useful case breaks with the bug."""
    from app.aimee.registry import ToolResult

    ok = ToolResult(ok=True, proposal={"summary": "Record $7.00 cash for Pikesville"})
    payload = ok.for_model("write")
    assert payload["awaiting_confirmation"] is True
    assert "no_change_staged" not in payload


# ── the chat answer carries the same judgement as the phone ─────────────────

def test_fuel_rows_carry_a_status_and_marker(monkeypatch):
    """Handed bare percentages, the model printed a plain table and 15% sat
    there looking like any other number. The judgement has to travel WITH the
    data or the chat cannot show what the report shows."""
    monkeypatch.setattr(samsara, "fuel_levels", lambda: [
        {"id": "v1", "name": "Full", "percent": 83, "at": ""},
        {"id": "v2", "name": "Low", "percent": 15, "at": ""},
        {"id": "v3", "name": "Empty", "percent": 6, "at": ""},
        {"id": "v4", "name": "Silent", "percent": None, "at": ""},
    ])
    rows = {t["name"]: t for t in fleet.get_truck_fuel(db=None).data["trucks"]}
    assert (rows["Empty"]["status"], rows["Empty"]["marker"]) == ("critical", "🔴")
    assert (rows["Low"]["status"], rows["Low"]["marker"]) == ("low", "🟠")
    assert (rows["Full"]["status"], rows["Full"]["marker"]) == ("ok", "🟢")
    assert (rows["Silent"]["status"], rows["Silent"]["marker"]) == ("unknown", "⚪")


def test_fuel_rows_come_back_worst_first(monkeypatch):
    """The truck to do something about should not be fourth in the list, and
    unknowns sort last rather than as if they were empty."""
    monkeypatch.setattr(samsara, "fuel_levels", lambda: [
        {"id": "v1", "name": "Full", "percent": 83, "at": ""},
        {"id": "v2", "name": "Silent", "percent": None, "at": ""},
        {"id": "v3", "name": "Empty", "percent": 6, "at": ""},
        {"id": "v4", "name": "Low", "percent": 15, "at": ""},
    ])
    order = [t["name"] for t in fleet.get_truck_fuel(db=None).data["trucks"]]
    assert order == ["Empty", "Low", "Full", "Silent"]


def test_chat_and_the_telegram_report_share_one_definition_of_low():
    """Two surfaces, one threshold. If these ever diverge, the phone says a
    truck is fine and the chat says it needs fuel, about the same number."""
    from app.tasks.fleet_tasks import _fuel_report

    readings = [{"id": "v1", "name": "Borderline",
                 "percent": samsara.LOW_FUEL_PERCENT, "at": ""}]
    _, marker = samsara.fuel_status(samsara.LOW_FUEL_PERCENT)
    assert marker in _fuel_report(readings)          # the report uses it
    # ...and exactly at the threshold counts as low on both, not just under it.
    assert samsara.fuel_status(samsara.LOW_FUEL_PERCENT)[0] == "low"
