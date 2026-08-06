"""The write tool: cash counted at an event.

This one does not write. It returns a proposal the chat renders as a card with
Apply and Cancel, and the write happens only when a person presses Apply — see
``app/aimee/registry.py`` and ``apply_proposal`` below.

The reasoning is the same as Record Payments, where the office already works this
way: a model that has misheard "eighty" for "eighteen" produces a two-second
cancel instead of a wrong figure in the ledger that surfaces whenever somebody
next reconciles. Reads are free to be wrong because nothing happens; a write is
not.

Applying goes through the SAME endpoint the Record Payments screen and the cash
automation use, rather than touching the ledger here. One implementation of the
override, the recompute, the audit line and the min-guarantee settlement — the
alternative is two, and they drift.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from app.aimee.registry import ToolResult, tool
from app.models import Event, FinancialEntry


def _find_event(db: Session, event_query: str) -> Optional[Event]:
    """The event a phrase means, if exactly one does.

    Deliberately strict: an exact crm id, or a single name match. Anything
    ambiguous returns None so the tool can say which ones it found rather than
    proposing a write against a guess.
    """
    query = (event_query or "").strip()
    if not query:
        return None

    exact = db.query(Event).filter(Event.crm_event_id == query).first()
    if exact is not None:
        return exact

    matches = (
        db.query(Event)
        .filter(Event.event_name.ilike(f"%{query}%"))
        .order_by(Event.event_date.desc())
        .limit(5)
        .all()
    )
    return matches[0] if len(matches) == 1 else None


@tool(
    name="record_cash",
    kind="write",
    running_label="Preparing the cash update",
    description=(
        "Record the cash counted at an event. Use when told an amount was taken "
        "— 'Pikesville took $7', 'Arbutus had sixty dollars cash'. Identify the "
        "event by its name or KonaOS event id. This does NOT write immediately: "
        "it prepares the change for the user to confirm, so say what you are "
        "about to record rather than reporting it as done."
    ),
    parameters={
        "type": "object",
        "properties": {
            "event": {
                "type": "string",
                "description": "Event name or KonaOS event id",
            },
            "amount": {
                "type": "number",
                "description": "Cash collected in dollars, including tax",
            },
        },
        "required": ["event", "amount"],
    },
)
def record_cash(db: Session, event: str, amount: float) -> ToolResult:
    if amount is None or float(amount) < 0:
        return ToolResult(ok=False, error="Cash amount must be zero or more.")

    found = _find_event(db, event)
    if found is None:
        near = (
            db.query(Event)
            .filter(Event.event_name.ilike(f"%{(event or '').strip()}%"))
            .order_by(Event.event_date.desc())
            .limit(5)
            .all()
        )
        if near:
            return ToolResult(
                ok=False,
                error=(
                    f"\"{event}\" matches {len(near)} events — say which: "
                    + "; ".join(f"{e.event_name} ({e.event_date})" for e in near)
                ),
            )
        return ToolResult(ok=False, error=f"No event matches \"{event}\".")

    entry = (
        db.query(FinancialEntry)
        .filter(FinancialEntry.crm_event_id == found.crm_event_id)
        .one_or_none()
    )
    if entry is None:
        return ToolResult(
            ok=False,
            error=(
                f"\"{found.event_name}\" hasn't been processed yet, so there is "
                "no ledger row to post cash to. Run the pipeline for it first."
            ),
        )

    previous = float(entry.cash_collected or 0.0)
    return ToolResult(ok=True, proposal={
        "kind": "record_cash",
        "summary": (
            f"Record ${float(amount):,.2f} cash for {found.event_name} "
            f"({found.event_date})"
        ),
        "event_name": found.event_name,
        "event_date": found.event_date,
        "crm_event_id": found.crm_event_id,
        "previous_cash": previous,
        "new_cash": round(float(amount), 2),
        "replaces_existing": previous > 0,
    })


def apply_proposal(db: Session, proposal: dict[str, Any], by: str) -> dict[str, Any]:
    """Carry out a proposal a person has confirmed.

    Routed through the existing financials endpoint rather than writing here, so
    the override, the recompute of everything cash feeds, the audit entry and
    the settling of a min-guarantee invoice all keep one implementation.
    """
    kind = str(proposal.get("kind") or "")
    if kind != "record_cash":
        return {"ok": False, "summary": f"Don't know how to apply '{kind}'."}

    from app.api.routes.financials import CashUpdate, set_cash_by_event

    try:
        result = set_cash_by_event(
            str(proposal["crm_event_id"]),
            CashUpdate(cash_collected=float(proposal["new_cash"]),
                       source="manual", by=by),
            db=db,
            _="aimee",
        )
    except Exception as e:  # noqa: BLE001 — reported, never raised at the chat
        return {"ok": False, "summary": f"Couldn't record it: {e}"}

    summary = (
        f"Recorded ${float(proposal['new_cash']):,.2f} cash for "
        f"{proposal['event_name']} ({proposal['event_date']})"
    )
    if result.get("invoice_needed"):
        summary += (
            f" — minimum guarantee short by ${result.get('shortfall', 0):,.2f}, "
            "invoice settling"
        )
    return {"ok": True, "summary": summary, "detail": result}
