"""The two KonaOS report tools, and the difference between zero and unknown.

These endpoints answer 200 with a complete, well-formed envelope whether or not
they understood the request — so "no rows" is not evidence of "no business".
Asked for the top ten clients of the year, Aimee twice reported there were none,
because the tool handed back an empty list with ok=True on a response whose
echoed date window was 0. A confident wrong answer is worse than an error: you
act on it.

What is tested here is the refusal, not the happy path.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_konaice.db")
os.environ["CRM_PROVIDER"] = "mock"
os.environ["SQUARE_PROVIDER"] = "mock"
os.environ["OPENAI_PROVIDER"] = "mock"
os.environ["TELEGRAM_PROVIDER"] = "mock"

import pytest  # noqa: E402

from app.aimee.tools import reports  # noqa: E402


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Neither the client nor the event loop should be reached in these tests."""
    class _Stub:
        def __getattr__(self, _name):
            def _call(**_kwargs):
                return None
            return _call

    monkeypatch.setattr("app.konaos.client.KonaosClient", lambda *a, **k: _Stub())
    yield


def _returns(monkeypatch, payload):
    monkeypatch.setattr(reports, "_konaos_call", lambda _factory: payload)


# ── the envelope that started this ──────────────────────────────────────────

REAL_EMPTY_ENVELOPE = {
    "sortColumn": None, "count": 0, "totalCount": None, "limit": 10,
    "sortType": None, "data": [], "offset": 0, "toDate": 0,
    "searchText": "", "fromDate": 0,
}


def test_a_zeroed_date_echo_is_reported_as_a_failure_not_as_zero(monkeypatch):
    """The exact payload production returned on 2026-08-08."""
    _returns(monkeypatch, REAL_EMPTY_ENVELOPE)
    r = reports.get_client_ranking(db=None, from_date="2026-01-01", to_date="2026-08-08")
    assert r.ok is False
    assert "ignored the date range" in r.error
    assert "not a quiet period" in r.error


def test_the_sales_report_refuses_the_same_way(monkeypatch):
    _returns(monkeypatch, REAL_EMPTY_ENVELOPE)
    r = reports.get_sales_report(db=None, from_date="2026-08-01", to_date="2026-08-08")
    assert r.ok is False
    assert "2026-08-01" in r.error


def test_a_payload_with_nothing_row_shaped_is_unknown_not_empty(monkeypatch):
    _returns(monkeypatch, {"title": "Endpoint not found", "status": 404})
    r = reports.get_client_ranking(db=None, from_date="2026-01-01", to_date="2026-08-08")
    assert r.ok is False
    assert "unknown rather than as zero" in r.error


def test_a_non_dict_response_is_unknown(monkeypatch):
    _returns(monkeypatch, None)
    r = reports.get_sales_report(db=None, from_date="2026-08-01", to_date="2026-08-08")
    assert r.ok is False


# ── and the cases that must NOT be turned into errors ───────────────────────

def test_a_genuine_empty_result_is_still_a_valid_answer(monkeypatch):
    """Range echoed back correctly, no rows. That is a real zero, and saying so
    is the whole point of not failing every empty response."""
    _returns(monkeypatch, {
        "data": [], "fromDate": 1767225600000, "toDate": 1770940800000,
    })
    r = reports.get_client_ranking(db=None, from_date="2026-01-01", to_date="2026-02-13")
    assert r.ok is True
    assert r.data["clients"] == []


def test_rows_are_returned_even_if_the_echo_looks_wrong(monkeypatch):
    """A mismatched echo must never fail a usable answer — if KonaOS gave us
    data it understood enough, and this check exists only to explain a zero."""
    _returns(monkeypatch, {
        "data": [{"clientName": "Catonsville Elementary", "revenue": 4200}],
        "fromDate": 0, "toDate": 0,
    })
    r = reports.get_client_ranking(db=None, from_date="2026-01-01", to_date="2026-08-08")
    assert r.ok is True
    assert r.data["clients"][0]["clientName"] == "Catonsville Elementary"


def test_a_bare_list_response_is_accepted(monkeypatch):
    _returns(monkeypatch, [{"clientName": "A"}, {"clientName": "B"}])
    r = reports.get_client_ranking(db=None, from_date="2026-01-01", to_date="2026-08-08")
    assert r.ok is True
    assert len(r.data["clients"]) == 2


def test_the_client_limit_is_respected(monkeypatch):
    _returns(monkeypatch, {"data": [{"n": i} for i in range(50)],
                           "fromDate": 1, "toDate": 2})
    r = reports.get_client_ranking(
        db=None, from_date="2026-01-01", to_date="2026-08-08", limit=3
    )
    assert len(r.data["clients"]) == 3
