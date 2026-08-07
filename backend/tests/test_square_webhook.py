"""The Square timecard webhook — the instant path for clock notifications.

Three things are worth asserting here, and they are all about what the endpoint
REFUSES rather than what it forwards:

  * an unsigned or wrongly-signed request gets nothing, because this route is
    public and its effect is a push notification to somebody's phone;
  * a `labor.timecard.updated` with no end time is NOT a clock-out, which is
    the bug the n8n workflow this replaces shipped with — a break or a wage
    correction announced somebody had gone home, at the epoch;
  * the webhook and the 20-minute poll share one cursor, so a shift reported
    by one is never re-reported by the other.
"""
import base64
import hashlib
import hmac
import json
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_konaice.db")
os.environ["CRM_PROVIDER"] = "mock"
os.environ["SQUARE_PROVIDER"] = "mock"
os.environ["OPENAI_PROVIDER"] = "mock"
os.environ["TELEGRAM_PROVIDER"] = "mock"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.db.base import Base, SessionLocal, engine  # noqa: E402
from app.integrations import square_labor  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Alert, AppSetting  # noqa: E402
from app.tasks import fleet_tasks  # noqa: E402

URL = "https://finance.example.com/api/webhooks/square/labor"
KONA_KEY = "kona-signature-key"
PATH = "/api/webhooks/square/labor"

client = TestClient(app)


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
    monkeypatch.setattr(settings, "square_webhook_url", URL)
    monkeypatch.setattr(settings, "square_kona_webhook_signature_key", KONA_KEY)
    monkeypatch.setattr(settings, "square_tom_webhook_signature_key", "")
    monkeypatch.setattr(settings, "telegram_provider", "mock")
    # The roster call is the one piece that would reach the network.
    monkeypatch.setattr(
        square_labor, "team_members",
        lambda brand, refresh=False: {"TM1": "Drew LaPointe"},
    )
    yield


def _payload(event_type="labor.timecard.created", end_at=None, tid="TC1"):
    return {
        "type": event_type,
        "event_id": "evt-1",
        "data": {
            "type": "timecard",
            "id": tid,
            "object": {
                "timecard": {
                    "id": tid,
                    "team_member_id": "TM1",
                    "start_at": "2026-08-07T18:00:00Z",
                    "end_at": end_at,
                    "status": "CLOSED" if end_at else "OPEN",
                }
            },
        },
    }


def _post(payload, key=KONA_KEY, url=URL):
    """Sign exactly the way Square does: HMAC-SHA256 over url + raw body."""
    raw = json.dumps(payload).encode()
    digest = hmac.new(key.encode(), url.encode() + raw, hashlib.sha256).digest()
    return client.post(
        PATH,
        content=raw,
        headers={
            "x-square-hmacsha256-signature": base64.b64encode(digest).decode(),
            "Content-Type": "application/json",
        },
    )


def _alerts():
    db = SessionLocal()
    rows = db.query(Alert).filter(Alert.source == "clock").all()
    issues = [a.issue for a in rows]
    db.close()
    return issues


# ── the signature is the entire security model ──────────────────────────────

def test_an_unsigned_request_is_refused():
    resp = client.post(PATH, json=_payload())
    assert resp.status_code == 401
    assert _alerts() == []


def test_a_request_signed_with_the_wrong_key_is_refused():
    resp = _post(_payload(), key="not-the-key")
    assert resp.status_code == 401
    assert _alerts() == []


def test_a_signature_over_a_different_url_is_refused(monkeypatch):
    """The registered URL is part of the signed payload. A stray trailing slash
    between the dashboard and SQUARE_WEBHOOK_URL must fail, not almost-pass."""
    resp = _post(_payload(), url=URL + "/")
    assert resp.status_code == 401
    assert _alerts() == []


def test_with_no_key_configured_everything_is_refused(monkeypatch):
    """Fail closed. An unconfigured public push endpoint is a spam button."""
    monkeypatch.setattr(settings, "square_kona_webhook_signature_key", "")
    resp = _post(_payload())
    assert resp.status_code == 401


# ── what it does with a genuine event ───────────────────────────────────────

def test_a_clock_in_is_announced():
    resp = _post(_payload())
    assert resp.status_code == 200
    assert resp.json()["brand"] == "kona"
    issues = _alerts()
    assert len(issues) == 1
    assert "Drew LaPointe clocked in" in issues[0]


def test_a_clock_out_reports_hours_worked():
    _post(_payload())
    resp = _post(_payload("labor.timecard.updated", end_at="2026-08-07T22:30:00Z"))
    assert resp.status_code == 200
    issues = _alerts()
    assert any("clocked out" in i and "4.5h" in i for i in issues)


def test_an_update_with_no_end_time_is_not_a_clock_out():
    """A break, a wage edit or a deleted shift all arrive as
    labor.timecard.updated. The n8n workflow treated every one of them as a
    clock-out and rendered new Date(null) as the time."""
    _post(_payload())
    resp = _post(_payload("labor.timecard.updated"))
    assert resp.status_code == 200
    assert resp.json()["notified"] == []
    assert not any("clocked out" in i for i in _alerts())


def test_square_retrying_the_same_event_does_not_double_notify():
    _post(_payload())
    _post(_payload())
    assert len(_alerts()) == 1


def test_the_poll_does_not_repeat_what_the_webhook_already_sent(monkeypatch):
    """The shared cursor is the whole reason both paths can stay switched on."""
    _post(_payload())
    monkeypatch.setattr(square_labor, "configured", lambda: True)
    monkeypatch.setattr(square_labor, "timecards", lambda day: [{
        "brand": "kona", "name": "Drew LaPointe",
        "id": "TC1", "clock_in": "2026-08-07T18:00:00Z",
        "clock_out": "", "open": True, "hours": None,
    }])
    result = fleet_tasks.poll_clock_events()
    assert result["notified"] == 0
    assert len(_alerts()) == 1


# ── payloads that are not our business ──────────────────────────────────────

def test_an_unrelated_event_type_is_acknowledged_not_retried():
    """A 2xx, deliberately: a non-2xx makes Square retry something we will
    never want, forever."""
    resp = _post(_payload("payment.created"))
    assert resp.status_code == 200
    assert resp.json()["ignored"] == "payment.created"
    assert _alerts() == []
