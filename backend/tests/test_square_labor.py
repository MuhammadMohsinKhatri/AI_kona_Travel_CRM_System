"""Square Labor — the request body, not just the response.

This file exists because of a bug that HTTP could not see. Square accepts an
unknown filter key with a 200 and silently applies no filter at all, so asking
for one day returned every timecard on the account going back years. Nothing
raised, nothing logged, and 210 rows is indistinguishable from a busy day
unless you look at the dates.

That mattered beyond a wrong answer in chat: poll_clock_events treats each
unseen timecard as a new clock-in, so an unfiltered query would have pushed
hundreds of alerts about shifts from three years ago.

So these tests assert the SHAPE OF THE REQUEST. A response-only test would
have passed against the broken filter, which is exactly what happened.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_konaice.db")
os.environ["CRM_PROVIDER"] = "mock"
os.environ["SQUARE_PROVIDER"] = "mock"
os.environ["OPENAI_PROVIDER"] = "mock"
os.environ["TELEGRAM_PROVIDER"] = "mock"

from datetime import date  # noqa: E402

import pytest  # noqa: E402

from app.config import settings  # noqa: E402
from app.integrations import square_labor  # noqa: E402


@pytest.fixture(autouse=True)
def _tokens(monkeypatch):
    monkeypatch.setattr(settings, "square_kona_token", "test-kona")
    monkeypatch.setattr(settings, "square_tom_token", "")
    monkeypatch.setattr(square_labor, "_names", {"kona": {"tm-1": "Sam Driver"}})
    yield


def _capture(monkeypatch):
    """Record every request body square_labor sends."""
    sent = []

    def fake_post(brand, path, body):
        sent.append({"path": path, "body": body})
        return {"timecards": [{
            "id": "tc-1", "team_member_id": "tm-1",
            "start_at": "2026-08-06T13:00:00Z",
            "end_at": "2026-08-06T21:00:00Z",
        }]}

    monkeypatch.setattr(square_labor, "_post", fake_post)
    return sent


def test_the_day_filter_uses_filter_start_not_filter_start_at(monkeypatch):
    """The actual bug. `filter.start_at` is not rejected by Square — it is
    ignored, and every timecard on the account comes back."""
    sent = _capture(monkeypatch)
    square_labor.timecards(date(2026, 8, 6))

    body = sent[0]["body"]
    flt = body["query"]["filter"]
    assert "start" in flt, "the day filter must be filter.start"
    assert "start_at" not in flt, (
        "filter.start_at is silently ignored by Square — it returns the whole "
        "account unfiltered"
    )
    assert set(flt["start"]) == {"start_at", "end_at"}


def test_the_window_is_a_business_day_in_baltimore_not_utc(monkeypatch):
    """A shift ending at 8pm local is the NEXT day in UTC. Drawing the boundary
    in the wrong zone silently drops the evening shifts — which, for an ice
    business, is most of them."""
    sent = _capture(monkeypatch)
    square_labor.timecards(date(2026, 8, 6))

    window = sent[0]["body"]["query"]["filter"]["start"]
    assert window["start_at"].startswith("2026-08-06T00:00:00")
    assert window["end_at"].startswith("2026-08-07T00:00:00")
    # Eastern in August is UTC-4; the offset must be present, not a bare naive
    # timestamp Square would read as UTC.
    assert "-04:00" in window["start_at"]


def test_an_open_shift_is_reported_as_open(monkeypatch):
    """`open` drives "who is on the clock right now" — it has to be explicit
    rather than something each caller re-derives from a missing end_at."""
    def fake_post(brand, path, body):
        return {"timecards": [{
            "id": "tc-open", "team_member_id": "tm-1",
            "start_at": "2026-08-06T13:00:00Z", "end_at": None,
        }]}
    monkeypatch.setattr(square_labor, "_post", fake_post)

    rows = square_labor.timecards(date(2026, 8, 6))
    assert rows[0]["open"] is True
    assert rows[0]["hours"] is None
    assert rows[0]["name"] == "Sam Driver"


def test_hours_are_computed_for_a_closed_shift(monkeypatch):
    _capture(monkeypatch)
    rows = square_labor.timecards(date(2026, 8, 6))
    assert rows[0]["open"] is False
    assert rows[0]["hours"] == 8.0


def test_one_brands_dead_token_does_not_blank_out_the_other(monkeypatch):
    """Half the roster is more useful than none of it."""
    monkeypatch.setattr(settings, "square_tom_token", "test-tom")
    monkeypatch.setattr(square_labor, "_names",
                        {"kona": {"tm-1": "Sam Driver"}, "tom": {}})

    def fake_post(brand, path, body):
        if brand == "tom":
            raise square_labor.SquareLaborError("Square returned 401 for tom")
        return {"timecards": [{
            "id": "tc-1", "team_member_id": "tm-1",
            "start_at": "2026-08-06T13:00:00Z",
            "end_at": "2026-08-06T21:00:00Z",
        }]}

    monkeypatch.setattr(square_labor, "_post", fake_post)
    rows = square_labor.timecards(date(2026, 8, 6))
    assert len(rows) == 1 and rows[0]["brand"] == "kona"


def test_all_brands_failing_raises_rather_than_reporting_an_empty_day(monkeypatch):
    """"Nobody worked today" and "we couldn't ask" must not look the same."""
    monkeypatch.setattr(settings, "square_tom_token", "test-tom")

    def fake_post(brand, path, body):
        raise square_labor.SquareLaborError(f"Square returned 401 for {brand}")

    monkeypatch.setattr(square_labor, "_post", fake_post)
    with pytest.raises(square_labor.SquareLaborError):
        square_labor.timecards(date(2026, 8, 6))
