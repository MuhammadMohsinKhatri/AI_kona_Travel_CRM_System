"""Events — what is booked, and when.

The n8n original pointed at konaoscrmsapis-production.up.railway.app, which no
longer exists. Everything it served, this system already proxies through
``app.konaos.client``, so these read straight from there: one fewer service to
keep alive, and the same session key the nightly pipeline already maintains.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.aimee.registry import ToolResult, tool
from app.models import Event


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


def _parse_day(value: str, fallback: date) -> date:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return fallback


@tool(
    name="get_events",
    running_label="Checking the calendar",
    description=(
        "Events booked in KonaOS between two dates. Use for any question about "
        "what is on: today, tomorrow, this week, a named date, or how busy a "
        "period looks. Returns each event's name, date, times, location, brand "
        "and status. Dates are YYYY-MM-DD; omit them for the next 7 days."
    ),
    parameters={
        "type": "object",
        "properties": {
            "from_date": {"type": "string", "description": "YYYY-MM-DD, inclusive"},
            "to_date": {"type": "string", "description": "YYYY-MM-DD, inclusive"},
        },
    },
)
def get_events(db: Session, from_date: str = "", to_date: str = "") -> ToolResult:
    from app.integrations.factory import get_crm

    today = date.today()
    start = _parse_day(from_date, today)
    end = _parse_day(to_date, start + timedelta(days=7))
    if end < start:
        start, end = end, start

    rows = get_crm().list_events(_ms(start), _ms(end + timedelta(days=1))) or []

    # Our own processed record, where we have one, carries the classification
    # and the invoice figure — worth more to a question about an event than the
    # raw grid row, and free to attach since it is a local lookup.
    ours = {
        e.crm_event_id: e
        for e in db.query(Event)
        .filter(Event.event_date >= start.isoformat())
        .filter(Event.event_date <= end.isoformat())
        .all()
    }

    events: list[dict[str, Any]] = []
    for row in rows:
        crm_id = str(row.get("id") or "")
        local = ours.get(crm_id)
        events.append({
            "event_id": crm_id,
            "name": row.get("name") or (local.event_name if local else ""),
            "date": local.event_date if local else "",
            "city": row.get("city") or "",
            "status": row.get("statusName") or row.get("status") or "",
            "brand": row.get("brandName") or (local.brand if local else ""),
            "event_type": local.event_type if local else "",
            "invoice_amount": local.final_invoice_amount if local else None,
            "processed": local is not None,
        })

    events.sort(key=lambda e: (e["date"] or "", e["name"]))
    return ToolResult(ok=True, data={
        "from": start.isoformat(),
        "to": end.isoformat(),
        "count": len(events),
        "events": events[:60],
    })
