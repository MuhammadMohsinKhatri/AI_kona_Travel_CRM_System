"""The two KonaOS reports: sales, and client ranking.

Both are POST endpoints this system already proxies. They return large grids, so
each tool trims to what a question actually needs before handing anything back —
a 40 KB payload in the transcript is paid for again on every subsequent turn of
the conversation, and answers nothing better than the top twenty rows.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any

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


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "rows", "items", "content"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


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
    payload = _konaos_call(lambda: client.get_sales_data_report(
        fromDate=_ms(from_date, today - timedelta(days=30)),
        toDate=_ms(to_date, today),
        offset=0, limit=200,
    ))

    rows = _rows(payload)
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
    payload = _konaos_call(lambda: client.get_client_ranking_report(
        fromDate=_ms(from_date, today - timedelta(days=365)),
        toDate=_ms(to_date, today),
        offset=0, limit=max(1, min(int(limit or 10), MAX_ROWS)),
    ))

    rows = _rows(payload)
    return ToolResult(ok=True, data={
        "from": from_date, "to": to_date,
        "clients": rows[:max(1, min(int(limit or 10), MAX_ROWS))],
    })
