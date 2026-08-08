"""The two KonaOS reports: sales, and client ranking.

Both are POST endpoints this system already proxies. They return large grids, so
each tool trims to what a question actually needs before handing anything back —
a 40 KB payload in the transcript is paid for again on every subsequent turn of
the conversation, and answers nothing better than the top twenty rows.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.aimee.registry import ToolResult, tool

MAX_ROWS = 25


def _ms(value: str, fallback: date) -> int:
    try:
        d = date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        d = fallback
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


def _konaos_call(coro_factory):
    """Run one KonaOS coroutine from sync tool code.

    A dedicated loop per call rather than a shared one: tools run in FastAPI's
    threadpool, where there is no running loop to borrow and no guarantee two
    calls share a thread. Cheap, and it cannot deadlock the way a cached loop
    can — the mistake that made every check upload 500 earlier in this project.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro_factory())
    finally:
        loop.close()


def _rows(payload: Any) -> Optional[list[dict[str, Any]]]:
    """The grid's rows, or None when the payload holds nothing row-shaped.

    None and [] mean very different things and used to be collapsed into the
    same empty list: "KonaOS said there were none" and "I could not find a list
    anywhere in this response" both came back as zero rows, and the caller
    reported the second as confidently as the first.
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "rows", "items", "content"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return None


def _filter_dropped(payload: Any, sent_from: int, sent_to: int) -> bool:
    """True when KonaOS's own echo proves our date range never reached the query.

    These grid endpoints answer 200 with a complete, well-formed envelope
    whether or not they understood the request — the same shape of lie Square
    tells when handed an unknown filter key (see integrations/square_labor.py).
    The tell is the echoed window: ask for January-to-August, be told the range
    was 0, and the zero rows that come back are not an answer about that range.

    Consulted ONLY when there are no rows. If KonaOS returned data it understood
    enough to be useful, and a surprising echo is not worth failing a usable
    answer over — this must never turn a working report into an error.
    """
    if not isinstance(payload, dict) or not (sent_from or sent_to):
        return False
    for key, sent in (("fromDate", sent_from), ("toDate", sent_to)):
        if not sent:
            continue
        try:
            echoed = int(payload.get(key))
        except (TypeError, ValueError):
            continue  # absent or non-numeric proves nothing either way
        if echoed == 0:
            return True
    return False


UNREADABLE = (
    "KonaOS replied, but the response contained nothing row-shaped, so I can't "
    "tell whether there were no results or the call failed. Treat this as "
    "unknown rather than as zero."
)


def _dropped_range_error(from_date: str, to_date: str) -> str:
    return (
        f"KonaOS ignored the date range {from_date} to {to_date} — it echoed a "
        "window of 0 and returned no rows, so this is a failed query, not a "
        "quiet period. Do not read it as zero. The report request needs fixing "
        "(app/aimee/tools/reports.py)."
    )


@tool(
    name="get_sales_report",
    running_label="Pulling the sales report",
    description=(
        "Aggregate sales figures from KonaOS for a date range — total revenue, "
        "sales by event, giveback. Use for revenue questions, monthly or weekly "
        "totals, 'how did we do', and comparisons between periods. NOT for a "
        "single event's invoice; use get_events for that. Dates are YYYY-MM-DD."
    ),
    parameters={
        "type": "object",
        "properties": {
            "from_date": {"type": "string", "description": "YYYY-MM-DD"},
            "to_date": {"type": "string", "description": "YYYY-MM-DD"},
        },
        "required": ["from_date", "to_date"],
    },
)
def get_sales_report(db: Session, from_date: str, to_date: str) -> ToolResult:
    from app.konaos.client import KonaosClient

    today = date.today()
    client = KonaosClient()
    from_ms = _ms(from_date, today - timedelta(days=30))
    to_ms = _ms(to_date, today)
    payload = _konaos_call(lambda: client.get_sales_data_report(
        fromDate=from_ms, toDate=to_ms, offset=0, limit=200,
    ))

    rows = _rows(payload)
    if rows is None:
        return ToolResult(ok=False, error=UNREADABLE)
    if not rows and _filter_dropped(payload, from_ms, to_ms):
        return ToolResult(ok=False, error=_dropped_range_error(from_date, to_date))
    return ToolResult(ok=True, data={
        "from": from_date, "to": to_date,
        "row_count": len(rows),
        "rows": rows[:MAX_ROWS],
        "truncated": len(rows) > MAX_ROWS,
        "totals": payload.get("totals") if isinstance(payload, dict) else None,
    })


@tool(
    name="get_client_ranking",
    running_label="Ranking clients",
    description=(
        "Clients ranked by revenue, event count or hours over a date range. Use "
        "for 'top clients', 'who spends most', 'best customers this year', and "
        "industry breakdowns. Dates are YYYY-MM-DD."
    ),
    parameters={
        "type": "object",
        "properties": {
            "from_date": {"type": "string", "description": "YYYY-MM-DD"},
            "to_date": {"type": "string", "description": "YYYY-MM-DD"},
            "limit": {"type": "integer", "description": "How many to return, default 10"},
        },
        "required": ["from_date", "to_date"],
    },
)
def get_client_ranking(
    db: Session, from_date: str, to_date: str, limit: int = 10
) -> ToolResult:
    from app.konaos.client import KonaosClient

    today = date.today()
    client = KonaosClient()
    capped = max(1, min(int(limit or 10), MAX_ROWS))
    from_ms = _ms(from_date, today - timedelta(days=365))
    to_ms = _ms(to_date, today)
    payload = _konaos_call(lambda: client.get_client_ranking_report(
        fromDate=from_ms, toDate=to_ms, offset=0, limit=capped,
    ))

    rows = _rows(payload)
    if rows is None:
        return ToolResult(ok=False, error=UNREADABLE)
    if not rows and _filter_dropped(payload, from_ms, to_ms):
        return ToolResult(ok=False, error=_dropped_range_error(from_date, to_date))
    return ToolResult(ok=True, data={
        "from": from_date, "to": to_date,
        "clients": rows[:capped],
    })
