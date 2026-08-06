"""What AI has cost, against the credits someone put on the account — shown
wherever an AI cost figure appears (Dashboard, Automation Runs, Record Payments,
Aimee).

Two questions, answered together:

  * **"What's left of the credits Brett added?"** A prepaid pot that depletes.
    Credits are topped up on a date, spend accrues against them from that date
    onward, and the remainder is what is left to spend before the account runs
    dry. This does not reset in January.
  * **"What have we spent this month?"** A calendar-month total, for noticing
    that a month is running hot before the pot is empty.

**Spend is real** — pulled from OpenAI's own Costs API, which is the actual
bill, not our own running total of ``ai_cost_usd``. That local figure only ever
covers what this app itself called (classification, check reads, cash parsing);
if the same key is used elsewhere, or a price changes before
``OPENAI_*_COST_PER_MTOK`` is updated to match, our total and OpenAI's invoice
drift apart. The Costs API cannot drift from itself.

**The credit total is not.** OpenAI exposes no prepaid-balance endpoint — the
old ``/dashboard/billing/credit_grants`` was retired and never worked for API
keys anyway. So there is no way to *read* what was topped up; it has to be
typed in on the Settings page after a top-up, and the remainder is computed
from it. Getting that number wrong makes the remainder wrong by exactly the
same amount, which is why the screen shows what it was told and when, rather
than presenting the figure as though OpenAI vouched for it.

The Costs API needs an org-level Admin key (platform.openai.com -> Organization
-> Admin keys) — a different credential from ``OPENAI_API_KEY``, which is a
project key scoped to completions and cannot read this endpoint. Left
unconfigured, every spend figure is simply unavailable; nothing else here is
affected.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AI_BUDGET_KEY, AppSetting

COSTS_API = "https://api.openai.com/v1/organization/costs"
REQUEST_TIMEOUT_SECONDS = 10
# The API's only supported bucket width is one day, and 180 is the most it will
# return per page. A credit pot can easily span more than 180 days, so the
# fetch below follows next_page rather than assuming one page is the lot.
DAILY_BUCKET_LIMIT = 180
# A runaway cursor would loop forever on a malformed response; a year and a
# half of daily buckets is far past any real top-up window.
MAX_PAGES = 4

# Business calendar, matching the nightly pipeline (app/tasks/celery_app.py) —
# "this month" means the Baltimore office's month, not the server's UTC one.
_TZ = ZoneInfo("America/New_York")


# ── stored settings ─────────────────────────────────────────────────────────

def _raw(db: Session) -> dict[str, Any]:
    row = db.get(AppSetting, AI_BUDGET_KEY)
    return dict(row.value or {}) if row else {}


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def get_budget(db: Session) -> float:
    """The monthly ceiling, if one was set. Separate from the credit pot."""
    return _as_float(_raw(db).get("monthly_usd"))


def set_budget(db: Session, monthly_usd: float) -> float:
    _write(db, {"monthly_usd": max(0.0, float(monthly_usd))})
    return get_budget(db)


def get_credits(db: Session) -> tuple[float, Optional[str]]:
    """(credits added, the date they were added as YYYY-MM-DD)."""
    value = _raw(db)
    added_on = value.get("credits_added_on") or None
    return _as_float(value.get("credits_added_usd")), added_on


def set_credits(db: Session, added_usd: float, added_on: str) -> None:
    """Record a top-up: how much went on, and the day it did.

    The date is what makes the remainder meaningful — spend before a top-up was
    paid for by the previous one, so counting it against the new credits would
    show the pot emptier than it is.
    """
    _write(db, {
        "credits_added_usd": max(0.0, float(added_usd)),
        "credits_added_on": (added_on or "").strip() or None,
    })


def _write(db: Session, patch: dict[str, Any]) -> None:
    row = db.get(AppSetting, AI_BUDGET_KEY)
    if row is None:
        row = AppSetting(key=AI_BUDGET_KEY, value={})
        db.add(row)
    row.value = {**(row.value or {}), **patch}
    db.commit()


# ── time windows ────────────────────────────────────────────────────────────

def _month_start_epoch() -> tuple[int, str]:
    """The 1st of the current month, business time — epoch second for the API
    call, and a YYYY-MM label for display."""
    now = datetime.now(_TZ)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp()), start.strftime("%Y-%m")


def _date_epoch(iso: str) -> Optional[int]:
    """Midnight business-time on a YYYY-MM-DD, or None if it isn't one."""
    try:
        d = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    return int(datetime.combine(d, time.min, tzinfo=_TZ).timestamp())


# ── the Costs API ───────────────────────────────────────────────────────────

def _daily_costs(start_time: int) -> tuple[Optional[list[tuple[int, float]]], str]:
    """Every daily cost bucket from start_time to now, as (bucket start, USD).

    Returns (None, why) when unavailable. Paginated: a credit pot older than
    180 days needs more than one page, and silently keeping only the first
    would understate spend — which is the one direction of error that matters,
    since it makes the remaining balance look healthier than it is.
    """
    if not settings.openai_admin_api_key:
        return None, "No OpenAI Admin API key configured (OPENAI_ADMIN_API_KEY)."

    out: list[tuple[int, float]] = []
    params: dict[str, Any] = {
        "start_time": start_time,
        "bucket_width": "1d",
        "limit": DAILY_BUCKET_LIMIT,
    }
    try:
        for _ in range(MAX_PAGES):
            resp = httpx.get(
                COSTS_API,
                headers={"Authorization": f"Bearer {settings.openai_admin_api_key}"},
                params=params,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            data = resp.json()

            for bucket in data.get("data") or []:
                bucket_start = bucket.get("start_time") or 0
                total = 0.0
                for result in bucket.get("results") or []:
                    amount = result.get("amount") or {}
                    try:
                        total += float(amount.get("value") or 0.0)
                    except (TypeError, ValueError):
                        continue
                out.append((int(bucket_start), total))

            if not data.get("has_more") or not data.get("next_page"):
                break
            params = {**params, "page": data["next_page"]}
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            detail = e.response.json().get("error", {}).get("message", "")
        except Exception:  # noqa: BLE001 — the status text alone is still useful
            pass
        return None, (
            f"OpenAI Costs API returned {e.response.status_code}"
            + (f": {detail}" if detail else "")
        )
    except (httpx.HTTPError, ValueError) as e:
        return None, f"Couldn't reach OpenAI's Costs API: {e}"

    return out, ""


def monthly_spend_usd() -> tuple[Optional[float], str]:
    """Real spend this calendar month. (amount, error)."""
    start, _ = _month_start_epoch()
    buckets, error = _daily_costs(start)
    if buckets is None:
        return None, error
    return round(sum(v for _, v in buckets), 4), ""


# ── the one call every screen makes ─────────────────────────────────────────

def status(db: Session) -> dict[str, Any]:
    """Everything the screens need to show spend, a monthly figure and what is
    left of the credits — in ONE upstream call.

    Both windows come out of the same set of daily buckets, fetched from
    whichever start date is earlier and then split locally. Asking OpenAI twice
    would double the latency of every screen that shows a cost, to compute a
    subtotal of numbers already in hand.

    Never raises: a failed lookup still returns the configured figures with the
    error attached instead of the amounts, so one bad API call doesn't blank
    the whole widget.
    """
    month_start, month = _month_start_epoch()
    week_start = int(
        (datetime.now(_TZ) - timedelta(days=7))
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
    )
    budget = get_budget(db)
    credits_added, credits_on = get_credits(db)
    credits_start = _date_epoch(credits_on) if credits_on else None

    # One fetch, from whichever window reaches furthest back; the rest are
    # subtotals of buckets already in hand.
    fetch_from = min(month_start, week_start)
    if credits_start is not None:
        fetch_from = min(fetch_from, credits_start)
    buckets, error = _daily_costs(fetch_from)

    if buckets is None:
        spent = month_spent = credits_spent = week_spent = None
        credits_remaining = None
        remaining = None
    else:
        month_spent = round(sum(v for t, v in buckets if t >= month_start), 4)
        week_spent = round(sum(v for t, v in buckets if t >= week_start), 4)
        spent = month_spent
        credits_spent = (
            round(sum(v for t, v in buckets if t >= credits_start), 4)
            if credits_start is not None
            else None
        )
        remaining = round(budget - month_spent, 4)
        credits_remaining = (
            round(credits_added - credits_spent, 4)
            if credits_spent is not None and credits_added > 0
            else None
        )

    return {
        "month": month,
        # Monthly ceiling — unchanged meaning, kept for the screens that use it.
        "monthly_budget_usd": budget,
        "spent_usd": spent,
        "remaining_usd": remaining,
        "week_spent_usd": week_spent,
        # The prepaid pot: what went on, when, and what survives of it.
        "credits_added_usd": credits_added,
        "credits_added_on": credits_on,
        "credits_spent_usd": credits_spent,
        "credits_remaining_usd": credits_remaining,
        "admin_key_configured": bool(settings.openai_admin_api_key),
        "error": error,
    }
