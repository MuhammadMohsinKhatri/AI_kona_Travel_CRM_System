"""The monthly AI budget: a figure someone sets, checked against OpenAI's own
Costs API rather than our own running total — see app/core/ai_budget.py for
why those are deliberately different numbers.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_konaice.db")
os.environ["CRM_PROVIDER"] = "mock"
os.environ["SQUARE_PROVIDER"] = "mock"
os.environ["OPENAI_PROVIDER"] = "mock"
os.environ["TELEGRAM_PROVIDER"] = "mock"

from datetime import date, timedelta  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.config import settings  # noqa: E402
from app.core import ai_budget  # noqa: E402
from app.db.base import Base, SessionLocal, engine  # noqa: E402


def setup_module(_):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_budget_defaults_to_zero(db):
    assert ai_budget.get_budget(db) == 0.0


def test_set_and_get_budget_round_trips(db):
    assert ai_budget.set_budget(db, 75.0) == 75.0
    assert ai_budget.get_budget(db) == 75.0


def test_set_budget_rejects_negative(db):
    """A negative budget is nonsensical and would make `remaining` misleading
    in the other direction — clamp rather than store it."""
    assert ai_budget.set_budget(db, -10.0) == 0.0


def test_spend_without_admin_key_is_unavailable_not_zero(monkeypatch):
    """No key configured must read as 'we don't know', never as '$0 spent' —
    the second would make every budget look untouched when it's simply
    unmeasured."""
    monkeypatch.setattr(settings, "openai_admin_api_key", "")
    spent, error = ai_budget.monthly_spend_usd()
    assert spent is None
    assert "Admin API key" in error


def test_status_without_admin_key(db, monkeypatch):
    monkeypatch.setattr(settings, "openai_admin_api_key", "")
    ai_budget.set_budget(db, 50.0)
    result = ai_budget.status(db)
    assert result["monthly_budget_usd"] == 50.0
    assert result["spent_usd"] is None
    assert result["remaining_usd"] is None
    assert result["admin_key_configured"] is False
    assert result["error"]


def test_spend_sums_all_result_amounts(monkeypatch):
    monkeypatch.setattr(settings, "openai_admin_api_key", "sk-admin-test")
    payload = {
        "data": [
            {"results": [{"amount": {"value": 1.5, "currency": "usd"}}]},
            {"results": [
                {"amount": {"value": 0.25, "currency": "usd"}},
                {"amount": {"value": 0.75, "currency": "usd"}},
            ]},
        ]
    }
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(
        200, json=payload, request=httpx.Request("GET", ai_budget.COSTS_API)
    ))
    spent, error = ai_budget.monthly_spend_usd()
    assert spent == 2.5
    assert error == ""


def test_spend_handles_api_error_gracefully(monkeypatch):
    """A 401 (bad/revoked admin key) must come back as an explained None, not
    an exception that takes down whatever page is showing the budget."""
    monkeypatch.setattr(settings, "openai_admin_api_key", "sk-admin-bad")

    def fake_get(*_a, **_k):
        request = httpx.Request("GET", ai_budget.COSTS_API)
        response = httpx.Response(
            401, json={"error": {"message": "Invalid API key"}}, request=request
        )
        raise httpx.HTTPStatusError("401", request=request, response=response)

    monkeypatch.setattr(httpx, "get", fake_get)
    spent, error = ai_budget.monthly_spend_usd()
    assert spent is None
    assert "401" in error


def test_status_computes_remaining_when_spend_available(db, monkeypatch):
    monkeypatch.setattr(settings, "openai_admin_api_key", "sk-admin-test")
    month_start, _ = ai_budget._month_start_epoch()
    monkeypatch.setattr(
        ai_budget, "_daily_costs", lambda _s: ([(month_start, 12.5)], "")
    )
    ai_budget.set_budget(db, 50.0)
    ai_budget.set_credits(db, 0.0, "")
    result = ai_budget.status(db)
    assert result["spent_usd"] == 12.5
    assert result["remaining_usd"] == 37.5


# ── the credit pot ──────────────────────────────────────────────────────────

def test_credits_remaining_is_added_minus_spend_since_the_topup(db, monkeypatch):
    monkeypatch.setattr(settings, "openai_admin_api_key", "sk-admin-test")
    month_start, _ = ai_budget._month_start_epoch()
    monkeypatch.setattr(
        ai_budget, "_daily_costs", lambda _s: ([(month_start, 30.0)], "")
    )
    ai_budget.set_credits(db, 100.0, date.today().replace(day=1).isoformat())
    result = ai_budget.status(db)
    assert result["credits_added_usd"] == 100.0
    assert result["credits_spent_usd"] == 30.0
    assert result["credits_remaining_usd"] == 70.0


def test_spend_before_the_topup_is_not_charged_to_the_new_credits(db, monkeypatch):
    """The whole point of storing a date. Money spent before Brett topped up
    was paid for by the PREVIOUS credits; counting it again would show the new
    pot part-empty the moment it was added."""
    monkeypatch.setattr(settings, "openai_admin_api_key", "sk-admin-test")
    month_start, _ = ai_budget._month_start_epoch()
    topup = date.today().replace(day=1) + timedelta(days=10)
    topup_epoch = ai_budget._date_epoch(topup.isoformat())
    monkeypatch.setattr(ai_budget, "_daily_costs", lambda _s: ([
        (month_start, 40.0),            # before the top-up — old money
        (topup_epoch + 86400, 5.0),     # after it — comes off the new pot
    ], ""))
    ai_budget.set_credits(db, 100.0, topup.isoformat())
    result = ai_budget.status(db)
    assert result["credits_spent_usd"] == 5.0
    assert result["credits_remaining_usd"] == 95.0
    # The monthly figure still counts everything in the month, both sides.
    assert result["spent_usd"] == 45.0


def test_no_credits_recorded_means_no_remaining_figure(db, monkeypatch):
    """Zero credits must read as 'nobody has told us', not '$0 left' — the
    second would show an alarming empty balance on a healthy account."""
    monkeypatch.setattr(settings, "openai_admin_api_key", "sk-admin-test")
    month_start, _ = ai_budget._month_start_epoch()
    monkeypatch.setattr(
        ai_budget, "_daily_costs", lambda _s: ([(month_start, 3.0)], "")
    )
    ai_budget.set_credits(db, 0.0, "")
    result = ai_budget.status(db)
    assert result["credits_remaining_usd"] is None


def test_daily_costs_follows_pagination(monkeypatch):
    """A pot older than 180 days spans more than one page. Keeping only the
    first would understate spend — the one direction of error that matters,
    because it makes the balance look healthier than it is."""
    monkeypatch.setattr(settings, "openai_admin_api_key", "sk-admin-test")
    pages = [
        {"data": [{"start_time": 100, "results": [{"amount": {"value": 1.0}}]}],
         "has_more": True, "next_page": "cursor-2"},
        {"data": [{"start_time": 200, "results": [{"amount": {"value": 2.0}}]}],
         "has_more": False, "next_page": None},
    ]
    seen: list[object] = []

    def fake_get(*_a, **kwargs):
        page = kwargs["params"].get("page")
        seen.append(page)
        body = pages[1] if page == "cursor-2" else pages[0]
        return httpx.Response(
            200, json=body, request=httpx.Request("GET", ai_budget.COSTS_API)
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    buckets, error = ai_budget._daily_costs(0)
    assert error == ""
    assert buckets == [(100, 1.0), (200, 2.0)]
    assert seen == [None, "cursor-2"]
