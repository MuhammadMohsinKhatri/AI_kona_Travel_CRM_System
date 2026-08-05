"""How much AI has cost this month, against a budget someone set — shown
wherever an AI cost figure appears (Dashboard, Automation Runs, Record Payments).

Two numbers, from two different places, on purpose:

  * **Spend** is real — pulled from OpenAI's own Costs API, which is the actual
    bill, not our own running total of ``ai_cost_usd``. That local figure only
    ever covers what this app itself called (classification, check reads, cash
    parsing); if the same API key is ever used for anything else, or a price
    changes before ``OPENAI_*_COST_PER_MTOK`` is updated to match, our total and
    OpenAI's invoice drift apart. The Costs API cannot drift from itself.
  * **Budget** is not something OpenAI has any concept of for a normal
    pay-as-you-go account — there is no prepaid balance to read back. It is
    whatever figure is set on the Settings page, stored the same way as the
    Telegram config: in ``AppSetting``, editable without a redeploy.

The Costs API needs an org-level Admin key (platform.openai.com -> Organization
-> Admin keys) — a different credential from ``OPENAI_API_KEY``, which is a
project key scoped to making completions and cannot read this endpoint. Left
unconfigured, spend/remaining simply aren't available; nothing else here is
affected.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AI_BUDGET_KEY, AppSetting

COSTS_API = "https://api.openai.com/v1/organization/costs"
REQUEST_TIMEOUT_SECONDS = 10
# One bucket per day, the API's only supported width; 31 covers any month
# without pagination.
MAX_DAYS_PER_MONTH = 31

# Business calendar, matching the nightly pipeline (app/tasks/celery_app.py) —
# "this month" means the Baltimore office's month, not the server's UTC one.
_TZ = ZoneInfo("America/New_York")


def get_budget(db: Session) -> float:
    row = db.get(AppSetting, AI_BUDGET_KEY)
    value = dict(row.value or {}) if row else {}
    try:
        return float(value.get("monthly_usd") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def set_budget(db: Session, monthly_usd: float) -> float:
    row = db.get(AppSetting, AI_BUDGET_KEY)
    if row is None:
        row = AppSetting(key=AI_BUDGET_KEY, value={})
        db.add(row)
    row.value = {**(row.value or {}), "monthly_usd": max(0.0, float(monthly_usd))}
    db.commit()
    return get_budget(db)


def _month_start_epoch() -> tuple[int, str]:
    """The 1st of the current month, business time — as an epoch second for the
    API call, and as a YYYY-MM label for display."""
    now = datetime.now(_TZ)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp()), start.strftime("%Y-%m")


def monthly_spend_usd() -> tuple[Optional[float], str]:
    """Real spend this month, from OpenAI's Costs API. (amount, error) — amount
    is None whenever it isn't available, error explains why."""
    if not settings.openai_admin_api_key:
        return None, "No OpenAI Admin API key configured (OPENAI_ADMIN_API_KEY)."

    start_time, _ = _month_start_epoch()
    try:
        resp = httpx.get(
            COSTS_API,
            headers={"Authorization": f"Bearer {settings.openai_admin_api_key}"},
            params={
                "start_time": start_time,
                "bucket_width": "1d",
                "limit": MAX_DAYS_PER_MONTH,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
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

    total = 0.0
    for bucket in data.get("data") or []:
        for result in bucket.get("results") or []:
            amount = result.get("amount") or {}
            try:
                total += float(amount.get("value") or 0.0)
            except (TypeError, ValueError):
                continue
    return round(total, 4), ""


def status(db: Session) -> dict[str, Any]:
    """Everything the Dashboard/Settings/Payments screens need to show a
    budget and a remaining figure, in one call. Never raises — a spend lookup
    that fails still returns the budget, with the error attached instead of
    the amount, so one broken API call doesn't blank out the whole widget."""
    _, month = _month_start_epoch()
    budget = get_budget(db)
    spent, error = monthly_spend_usd()
    remaining = None if spent is None else round(budget - spent, 4)
    return {
        "month": month,
        "monthly_budget_usd": budget,
        "spent_usd": spent,
        "remaining_usd": remaining,
        "admin_key_configured": bool(settings.openai_admin_api_key),
        "error": error,
    }
