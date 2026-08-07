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

import re
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.aimee.registry import ToolResult, tool
from app.core.event_matcher import _significant, _tokens
from app.models import Event, FinancialEntry


_DATE_IN_TEXT = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

# Shortest token allowed to match as a prefix of another. Below this, "s" from
# "Farmer's" would match half the vocabulary.
_STEM_MIN = 4


def _covers(word: str, tokens: list[str]) -> bool:
    """Whether a query word is present, allowing for one being a stem of the other.

    Exact equality is too strict for names people actually type: "farmers"
    against an event called "Arbutus Farmer's Market", whose apostrophe splits
    it into "farmer" and "s", matches nothing at all. Containment either way
    covers singular/plural and the possessive, gated on length so short tokens
    cannot match loosely.

    Only reached when a plain substring search already found nothing, and the
    caller still requires exactly one surviving candidate — so the looseness
    cannot on its own decide which event gets the cash.
    """
    word = _bare(word)
    for raw in tokens:
        t = _bare(raw)
        if (word == t
                or (len(word) >= _STEM_MIN and word in t)
                or (len(t) >= _STEM_MIN and t in word)):
            return True
    return False


def _bare(token: str) -> str:
    """Drop apostrophes so "farmer's" and "farmers" are the same word.

    The shared tokenizer keeps the apostrophe inside the token rather than
    splitting on it, so without this the possessive in "Arbutus Farmer's
    Market" defeats both equality and containment.
    """
    return token.replace("'", "").replace("’", "")


def _split_date(event_query: str, event_date: str) -> tuple[str, str]:
    """(name, date) — pulling a date out of the NAME when it was smuggled there.

    A model shown "Name (2026-07-28)" in an error message will sometimes hand
    it straight back that way however the schema is worded, and "(IC)
    Pikesville Farmers Market (2026-07-28)" matches no event at all. Cheaper to
    be liberal here than to bounce the user round the same loop.
    """
    name = (event_query or "").strip()
    on = (event_date or "").strip()
    if not on:
        found = _DATE_IN_TEXT.search(name)
        if found:
            on = found.group(1)
    name = _DATE_IN_TEXT.sub("", name).replace("()", "").strip(" ()-–—,")
    return name, on


def find_candidates(
    db: Session, event_query: str, event_date: str = "", brand: str = ""
) -> list[Event]:
    """Every event a phrase could mean, narrowed by date and brand when given.

    Matching is deliberately loose on the NAME and strict on everything else.
    Nobody types "(IC) Pikesville Farmers Market"; they type "Pikesville
    Farmers", and the leading "(IC)" is a KonaOS prefix no one says out loud.
    So a substring hit is tried first, and failing that every significant word
    of the query must appear somewhere in the name — which also survives word
    order, punctuation and the apostrophe in "Farmer's".

    Strict on date and brand because those are how two real events are told
    apart, and getting one wrong posts cash to the wrong ledger row.
    """
    name, on = _split_date(event_query, event_date)
    if not name:
        return []

    scoped = db.query(Event)
    if on:
        scoped = scoped.filter(Event.event_date == on)
    if brand.strip():
        scoped = scoped.filter(Event.brand.ilike(f"%{brand.strip()}%"))

    hits = (
        scoped.filter(Event.event_name.ilike(f"%{name}%"))
        .order_by(Event.event_date.desc())
        .limit(10)
        .all()
    )
    if hits:
        return hits

    # Word-by-word fallback. Bounded: with a date this is one day's events, and
    # without one it is capped, because loading the whole table to fuzzy-match
    # a typo is not worth it.
    wanted = _significant(_tokens(name))
    if not wanted:
        return []
    pool = scoped.order_by(Event.event_date.desc()).limit(400).all()
    return [
        e for e in pool
        if all(_covers(w, _tokens(e.event_name or "")) for w in wanted)
    ]


def _find_event(
    db: Session, event_query: str, event_date: str = "", brand: str = ""
) -> Optional[Event]:
    """The one event a phrase means, or None if it is genuinely ambiguous.

    None is not failure — it is the tool declining to guess so it can show what
    it found. Proposing a write against a coin-flip is the failure.
    """
    query = (event_query or "").strip()
    if not query:
        return None

    exact = db.query(Event).filter(Event.crm_event_id == query).first()
    if exact is not None:
        return exact

    found = find_candidates(db, event_query, event_date, brand)
    return found[0] if len(found) == 1 else None


@tool(
    name="record_cash",
    kind="write",
    running_label="Preparing the cash update",
    description=(
        "Record the cash counted at an event. Use when told an amount was taken "
        "— 'Pikesville took $7', 'Arbutus had sixty dollars cash'. "
        "Put ONLY the name in `event` — a partial name is fine, 'Pikesville "
        "Farmers' finds '(IC) Pikesville Farmers Market'. Never fold a date or "
        "a brand into it. "
        "`event_date` (YYYY-MM-DD) picks between events sharing a name. Name + "
        "date is normally enough. Only add `brand` if the tool asks for it, "
        "which happens solely when both brands ran the same event that day. "
        "This does NOT write immediately: it prepares the change for the user "
        "to confirm, so say what you are about to record rather than reporting "
        "it as done."
    ),
    parameters={
        "type": "object",
        "properties": {
            "event": {
                "type": "string",
                "description": (
                    "Event NAME only (partial is fine), or a KonaOS event id. "
                    "No date, no brand."
                ),
            },
            "amount": {
                "type": "number",
                "description": "Cash collected in dollars, including tax",
            },
            "event_date": {
                "type": "string",
                "description": "YYYY-MM-DD. Picks between events sharing a name.",
            },
            "brand": {
                "type": "string",
                "description": (
                    "'Kona Ice' or \"Travelin' Tom's\". Only needed when both "
                    "brands ran the same event on the same day."
                ),
            },
        },
        "required": ["event", "amount"],
    },
)
def record_cash(
    db: Session, event: str, amount: float, event_date: str = "", brand: str = ""
) -> ToolResult:
    if amount is None or float(amount) < 0:
        return ToolResult(ok=False, error="Cash amount must be zero or more.")

    found = _find_event(db, event, event_date, brand)
    if found is None:
        name, on = _split_date(event, event_date)
        # Widened deliberately: whatever narrowed this to nothing (the date, the
        # brand) is exactly what must be dropped to show the user their options.
        # Repeating the filter would report "no such event" for a name that
        # plainly exists, which is how the system looks like it lost an event it
        # had just listed.
        near = find_candidates(db, name)
        if not near:
            return ToolResult(ok=False, error=f'No event matches "{name}".')

        on_that_day = [e for e in near if not on or e.event_date == on]
        if on and not on_that_day:
            runs = "; ".join(sorted({e.event_date or "?" for e in near})[:6])
            return ToolResult(
                ok=False,
                error=(
                    f'No "{name}" on {on}. It runs on: {runs}. '
                    "Pass one of those as event_date."
                ),
            )

        # Same name, same day, two brands — the ONE case brand resolves. Asking
        # for it any other time is a question the user cannot answer usefully.
        brands = sorted({(e.brand or "").strip() for e in on_that_day if e.brand})
        if on and len(on_that_day) > 1 and len(brands) > 1:
            return ToolResult(
                ok=False,
                error=(
                    f'Both brands ran "{name}" on {on} — {" and ".join(brands)}. '
                    "Pass brand to say which."
                ),
            )

        listed = "; ".join(
            f"{e.event_name} ({e.event_date})" for e in near[:6]
        )
        return ToolResult(
            ok=False,
            error=(
                f'"{name}" matches {len(near)} events — say which by passing '
                f"event_date: {listed}"
            ),
        )

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
