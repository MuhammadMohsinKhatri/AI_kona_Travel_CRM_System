"""Who clocked in, and when — from Square's timecards.

Read-only. Payroll is not edited from a chat window.

The distinction that matters is between a shift that has ENDED and one still
open. "Who is working right now" and "who worked yesterday" are different
questions and Square answers them from the same records, so both live here and
the open/closed state is explicit in the result rather than inferred from a
missing field.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.aimee.registry import ToolResult, tool
from app.integrations import square_labor

_TZ = ZoneInfo("America/New_York")


@tool(
    name="get_clock_times",
    kind="read",
    running_label="Checking the timecards",
    description=(
        "Clock in and clock out times for staff, from Square. Give `on` as "
        "YYYY-MM-DD for a particular day (defaults to today), or set "
        "`only_open` to see just who is on the clock right now. Use for 'who "
        "is working', 'what time did Sarah clock in', 'did anyone forget to "
        "clock out'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "on": {
                "type": "string",
                "description": "Day as YYYY-MM-DD. Defaults to today.",
            },
            "only_open": {
                "type": "boolean",
                "description": "Only people currently clocked in.",
            },
            "who": {
                "type": "string",
                "description": "Filter to one person by name (partial is fine).",
            },
        },
    },
)
def get_clock_times(
    db: Session, on: str = "", only_open: bool = False, who: str = ""
) -> ToolResult:
    if not square_labor.configured():
        return ToolResult(
            ok=False,
            error="No Square tokens configured, so timecards can't be read.",
        )
    day = None
    if (on or "").strip():
        try:
            day = date.fromisoformat(on.strip())
        except ValueError:
            return ToolResult(
                ok=False, error=f"\"{on}\" isn't a date — use YYYY-MM-DD."
            )

    try:
        rows = (square_labor.on_the_clock() if only_open
                else square_labor.timecards(day))
    except square_labor.SquareLaborError as e:
        return ToolResult(ok=False, error=str(e), retryable=True)

    if who.strip():
        needle = who.strip().lower()
        rows = [r for r in rows if needle in r["name"].lower()]
        if not rows:
            return ToolResult(
                ok=False,
                error=f"No timecard for anyone matching \"{who}\" on that day.",
            )

    still_open = [r["name"] for r in rows if r["open"]]
    return ToolResult(ok=True, data={
        "day": (day or datetime.now(_TZ).date()).isoformat(),
        "timecards": rows,
        "still_clocked_in": still_open,
        "total_hours": round(sum(r["hours"] or 0 for r in rows), 2),
    })
