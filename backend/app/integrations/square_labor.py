"""Square Labor — who clocked in, and when.

Same Square account and the same tokens the sales side already uses
(``SQUARE_KONA_TOKEN`` / ``SQUARE_TOM_TOKEN``); this is a different family of
endpoints on it, not a different integration. Nothing new to configure.

Two calls, because Square's timecards carry a ``teamMemberId`` and no name:

  * ``/v2/team-members/search`` — the roster, id to name. Cached for the
    process: people are hired and let go on a scale of weeks.
  * ``/v2/labor/timecards/search`` — the shifts themselves, filtered to a day.

An OPEN timecard (clocked in, not yet out) has no ``endAt``. That is the state
the question "who is on the clock right now" is actually asking about, so it is
reported as ``open: True`` rather than left to the caller to infer from a null.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx

from app.config import settings

log = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 15
SQUARE_VERSION = "2024-10-17"
_TZ = ZoneInfo("America/New_York")

BRANDS = ("kona", "tom")


class SquareLaborError(RuntimeError):
    pass


def _token(brand: str) -> str:
    return (settings.square_kona_token if brand == "kona"
            else settings.square_tom_token) or ""


def configured() -> bool:
    return any(_token(b) for b in BRANDS)


def _post(brand: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    token = _token(brand)
    if not token:
        raise SquareLaborError(f"No Square token configured for {brand}.")
    url = f"{settings.square_api_base.rstrip('/')}{path}"
    try:
        resp = httpx.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Square-Version": SQUARE_VERSION,
                "Content-Type": "application/json",
            },
            json=body,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json() or {}
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            errors = (e.response.json() or {}).get("errors") or []
            detail = "; ".join(str(x.get("detail") or "") for x in errors).strip()
        except Exception:  # noqa: BLE001 — status alone still tells us something
            pass
        raise SquareLaborError(
            f"Square returned {e.response.status_code} for {brand}"
            + (f": {detail}" if detail else "")
        ) from e
    except (httpx.HTTPError, ValueError) as e:
        raise SquareLaborError(f"Couldn't reach Square: {e}") from e


_names: dict[str, dict[str, str]] = {}


def team_members(brand: str, refresh: bool = False) -> dict[str, str]:
    """``{team_member_id: display name}`` for one brand."""
    if brand in _names and not refresh:
        return _names[brand]
    data = _post(brand, "/v2/team-members/search", {"limit": 200})
    out: dict[str, str] = {}
    for m in data.get("team_members") or data.get("teamMembers") or []:
        mid = str(m.get("id") or "")
        if not mid:
            continue
        given = str(m.get("given_name") or m.get("givenName") or "").strip()
        family = str(m.get("family_name") or m.get("familyName") or "").strip()
        out[mid] = (f"{given} {family}".strip()
                    or str(m.get("email_address") or m.get("emailAddress") or mid))
    _names[brand] = out
    return out


def _day_bounds(on: date) -> tuple[str, str]:
    """RFC3339 start/end of a business day, in Baltimore time.

    Not UTC midnight: a shift that ends at 8pm local is the NEXT UTC day, and a
    day boundary drawn in the wrong timezone silently drops the evening shifts
    — which are most of them, for an ice business.
    """
    start = datetime.combine(on, time.min, tzinfo=_TZ)
    end = start + timedelta(days=1)
    return start.isoformat(), end.isoformat()


def timecards(on: Optional[date] = None, brand: str = "") -> list[dict[str, Any]]:
    """Shifts touching a day, across both brands unless one is named.

    ``[{"brand", "name", "clock_in", "clock_out", "open", "hours"}]``
    """
    day = on or datetime.now(_TZ).date()
    start, end = _day_bounds(day)
    brands = [brand] if brand in BRANDS else [b for b in BRANDS if _token(b)]
    if not brands:
        raise SquareLaborError("No Square tokens configured.")

    out: list[dict[str, Any]] = []
    errors: list[str] = []
    for b in brands:
        try:
            names = team_members(b)
            data = _post(b, "/v2/labor/timecards/search", {
                "query": {
                    "filter": {
                        "start_at": {"start_at": start, "end_at": end},
                    },
                    "sort": {"field": "START_AT", "order": "ASC"},
                },
                "limit": 200,
            })
        except SquareLaborError as e:
            # One brand's token being dead must not blank out the other's
            # answer — half the roster is more useful than none of it.
            errors.append(str(e))
            continue

        for tc in data.get("timecards") or data.get("shifts") or []:
            member = str(tc.get("team_member_id") or tc.get("teamMemberId") or "")
            clock_in = str(tc.get("start_at") or tc.get("startAt") or "")
            clock_out = str(tc.get("end_at") or tc.get("endAt") or "")
            hours = None
            if clock_in and clock_out:
                try:
                    delta = (datetime.fromisoformat(clock_out.replace("Z", "+00:00"))
                             - datetime.fromisoformat(clock_in.replace("Z", "+00:00")))
                    hours = round(delta.total_seconds() / 3600, 2)
                except ValueError:
                    hours = None
            out.append({
                "brand": b,
                "name": names.get(member, member or "unknown"),
                "clock_in": clock_in,
                "clock_out": clock_out,
                "open": not clock_out,
                "hours": hours,
            })

    if not out and errors:
        raise SquareLaborError("; ".join(errors))
    out.sort(key=lambda r: r["clock_in"])
    return out


def on_the_clock() -> list[dict[str, Any]]:
    """Anyone currently clocked in — today's open timecards.

    Yesterday's are checked too: a shift that began at 11pm and never closed is
    still open now, and is exactly the case worth surfacing.
    """
    today = datetime.now(_TZ).date()
    rows = timecards(today)
    try:
        rows += timecards(today - timedelta(days=1))
    except SquareLaborError:
        pass
    return [r for r in rows if r["open"]]
