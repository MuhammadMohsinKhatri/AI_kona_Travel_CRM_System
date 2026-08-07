"""Square's push feed for timecards — the instant half of clock notifications.

``app/tasks/fleet_tasks.py`` polls Square every 20 minutes because nothing
inside the worker can subscribe to a feed. This is the other direction: Square
POSTs here the moment a shift opens or closes, so "just clocked in" means
seconds instead of up to twenty minutes.

**The poll stays.** A webhook is a delivery attempt, not a guarantee — a
restart mid-request, a lapsed certificate, or a subscription someone disables
in the Square dashboard all lose events in silence, and the poll is the thing
that notices. Both paths call ``fleet_tasks.notify_clock_timecard``, which
dedupes against one shared cursor, so whichever arrives first sends and the
other becomes a no-op. Belt and braces, at the cost of one extra Square call
per 20 minutes.

**Signature verification is the whole security model, not hardening.** This
route is public and its only effect is pushing a message to somebody's phone;
unauthenticated, it is a spam button for anyone who learns the URL, and the
message body would be attacker-controlled HTML going into Telegram. The n8n
workflow this replaces had precisely that hole. With no signature key
configured the route refuses everything rather than trusting the payload —
failing closed, because the failure mode of failing open is silent.

Kona and Travelin' Tom are separate Square accounts, so each has its own
subscription and its own signature key. Which key verifies is what tells us
which brand's roster to look the team member up in.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.db.base import get_db
from app.integrations import square_labor
from app.tasks import fleet_tasks

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

# Square signs the concatenation of the notification URL exactly as registered
# and the raw request body. "Exactly as registered" is load-bearing: a trailing
# slash or http-vs-https difference between the dashboard and
# SQUARE_WEBHOOK_URL produces a valid-looking signature that never matches, and
# the only symptom is a silent 401 on every event.
SIGNATURE_HEADER = "x-square-hmacsha256-signature"

CLOCK_EVENT_TYPES = ("labor.timecard.created", "labor.timecard.updated")


def _brand_for_signature(raw: bytes, signature: str) -> Optional[str]:
    """Which brand's key signed this, or None if neither did."""
    url = settings.square_webhook_url
    if not url or not signature:
        return None
    candidates = (
        ("kona", settings.square_kona_webhook_signature_key),
        ("tom", settings.square_tom_webhook_signature_key),
    )
    for brand, key in candidates:
        if not key:
            continue
        digest = hmac.new(key.encode(), url.encode() + raw, hashlib.sha256).digest()
        expected = base64.b64encode(digest).decode()
        # compare_digest, not ==: a timing-variable comparison on a MAC is how
        # signatures get forged a byte at a time.
        if hmac.compare_digest(expected, signature):
            return brand
    return None


def _timecard_of(body: dict[str, Any]) -> dict[str, Any]:
    obj = ((body.get("data") or {}).get("object") or {})
    return obj.get("timecard") or obj.get("shift") or {}


def _member_name(brand: str, member_id: str) -> str:
    """Roster lookup, re-pulled once for someone hired since process start.

    The workflow this replaces carried 40 names pasted into a code node, so
    every new hire read as "Unknown Employee" until a human edited it. The
    roster is cached per process, so the refresh only costs a call the first
    time an unfamiliar id appears.
    """
    if not member_id:
        return "Unknown employee"
    names = square_labor.team_members(brand)
    if member_id not in names:
        names = square_labor.team_members(brand, refresh=True)
    return names.get(member_id, "Unknown employee")


def _hours(clock_in: str, clock_out: str) -> Optional[float]:
    if not (clock_in and clock_out):
        return None
    try:
        delta = (datetime.fromisoformat(clock_out.replace("Z", "+00:00"))
                 - datetime.fromisoformat(clock_in.replace("Z", "+00:00")))
    except ValueError:
        return None
    return round(delta.total_seconds() / 3600, 2)


@router.post("/square/labor")
async def square_labor_webhook(
    request: Request, db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Announce a clock-in or clock-out the moment Square reports it."""
    raw = await request.body()
    brand = _brand_for_signature(raw, request.headers.get(SIGNATURE_HEADER, ""))
    if brand is None:
        # No detail in the response: a caller who cannot sign gains nothing
        # from learning whether the key is missing, wrong, or the URL mismatched.
        log.warning("Square webhook rejected: signature did not verify")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Body is not JSON")

    event_type = str(body.get("type") or "")
    if event_type not in CLOCK_EVENT_TYPES:
        # Subscriptions drift — someone ticks an extra box in the dashboard and
        # suddenly this endpoint sees payments. Acknowledge and drop, because a
        # non-2xx would make Square retry something we will never want.
        return {"ok": True, "ignored": event_type}

    timecard = _timecard_of(body)
    if not timecard:
        return {"ok": True, "ignored": "no timecard in payload"}

    member_id = str(timecard.get("team_member_id")
                    or timecard.get("teamMemberId") or "")
    clock_in = str(timecard.get("start_at") or timecard.get("startAt") or "")
    # An OPEN timecard has no end. Keying the clock-out on end_at rather than on
    # the event type is what stops a break, a wage correction or a deleted
    # shift — all of which arrive as labor.timecard.updated — being announced as
    # "clocked out", with new Date(null) rendering the epoch as the time.
    clock_out = str(timecard.get("end_at") or timecard.get("endAt") or "")

    row = {
        "id": str(timecard.get("id") or ""),
        "brand": brand,
        "name": _member_name(brand, member_id),
        "clock_in": clock_in,
        "clock_out": clock_out,
        "open": not clock_out,
        "hours": _hours(clock_in, clock_out),
    }

    issues = fleet_tasks.notify_clock_timecard(db, row)
    db.commit()
    return {"ok": True, "brand": brand, "notified": issues}
