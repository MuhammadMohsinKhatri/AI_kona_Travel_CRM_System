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
    monkeypatch.setattr(ai_budget, "monthly_spend_usd", lambda: (12.5, ""))
    ai_budget.set_budget(db, 50.0)
    result = ai_budget.status(db)
    assert result["spent_usd"] == 12.5
    assert result["remaining_usd"] == 37.5
