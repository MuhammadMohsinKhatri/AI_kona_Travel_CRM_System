"""Aimee's load-bearing guarantees.

Not the model — that is not testable and not the risk. What is tested here is
everything around it: that a broken tool cannot break the chat, that a write
proposes rather than writes, and that confirming a proposal goes through the
same ledger path as every other cash entry.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_konaice.db")
os.environ.setdefault("MOCK_LATENCY_S", "0")
os.environ["CRM_PROVIDER"] = "mock"
os.environ["SQUARE_PROVIDER"] = "mock"
os.environ["OPENAI_PROVIDER"] = "mock"
os.environ["TELEGRAM_PROVIDER"] = "mock"
os.environ["PIPELINE_DRY_RUN"] = "false"

from app.aimee.registry import ToolResult, all_tools, get_tool, tool  # noqa: E402
from app.aimee.tools.finance import apply_proposal  # noqa: E402
from app.db.base import Base, SessionLocal, engine  # noqa: E402
from app.models import Event, FinancialEntry  # noqa: E402


def setup_module(_):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def _fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    return SessionLocal()


def _seed_event(db, *, name="Pikesville Farmers Market", crm_id="ev-pikes",
                date="2026-07-25", cash=0.0, ledger=True):
    event = Event(crm_event_id=crm_id, event_name=name, event_date=date,
                  brand="Kona Ice", status="processed")
    db.add(event)
    db.flush()
    if ledger:
        db.add(FinancialEntry(event_id=event.id, crm_event_id=crm_id,
                              event_name=name, event_date=date,
                              cash_collected=cash))
    db.commit()
    return event


# ── the registry's one promise ───────────────────────────────────────────────

def test_a_tool_that_raises_never_reaches_the_agent_loop():
    """The whole reason tools are wrapped. A dead integration must mean "I
    can't reach the trucks right now" and an otherwise working conversation —
    not a chat that dies on one bad API key."""

    @tool(name="explodes", description="x", parameters={"type": "object"})
    def explodes(db):
        raise RuntimeError("Samsara is down")

    result = get_tool("explodes").run(db=None)
    assert isinstance(result, ToolResult)
    assert not result.ok
    assert "Samsara is down" in result.error
    assert result.retryable          # a timeout is worth retrying


def test_a_tool_called_with_nonsense_arguments_says_so_instead_of_crashing():
    """Models invent arguments. That is their mistake to correct, and they can
    only correct it if told plainly rather than through a stack trace."""

    @tool(name="picky", description="x",
          parameters={"type": "object", "properties": {"n": {"type": "integer"}}})
    def picky(db, n: int):
        return ToolResult(ok=True, data=n)

    result = get_tool("picky").run(db=None, wrong_name=1)
    assert not result.ok
    assert "doesn't accept" in result.error
    assert not result.retryable      # retrying the same mistake won't help


def test_every_registered_tool_declares_what_the_model_needs_to_use_it():
    for t in all_tools():
        assert t.description.strip(), f"{t.name} has no description"
        assert t.parameters.get("type") == "object", f"{t.name} has no schema"
        assert t.kind in ("read", "write")
        assert t.running_label, f"{t.name} has no label for the UI"


# ── writes propose, they do not write ────────────────────────────────────────

def test_recording_cash_proposes_and_changes_nothing_yet():
    """The load-bearing safety property. A model that mishears "eighty" for
    "eighteen" must produce a card, not a ledger entry."""
    db = _fresh_db()
    try:
        _seed_event(db, cash=12.0)
        result = get_tool("record_cash").run(
            db=db, event="Pikesville Farmers Market", amount=63.0)

        assert result.ok
        assert result.proposal is not None
        assert result.proposal["new_cash"] == 63.0
        assert result.proposal["previous_cash"] == 12.0
        assert result.proposal["replaces_existing"] is True

        # Nothing written.
        entry = db.query(FinancialEntry).one()
        assert entry.cash_collected == 12.0

        # And the model is told it is waiting, so it describes rather than
        # claims — the difference between "I'll record" and "I've recorded".
        assert result.for_model()["awaiting_confirmation"] is True
    finally:
        db.close()


def test_an_ambiguous_event_is_refused_rather_than_guessed():
    """Two events at one school in a week is normal. Posting cash to the wrong
    one is a real cost, so this asks instead of picking."""
    db = _fresh_db()
    try:
        _seed_event(db, name="Milford Mill Elementary", crm_id="ev-a",
                    date="2026-07-20")
        _seed_event(db, name="Milford Mill Elementary", crm_id="ev-b",
                    date="2026-07-27")
        result = get_tool("record_cash").run(db=db, event="Milford Mill", amount=25.0)

        assert not result.ok
        assert "matches 2 events" in result.error
        assert result.proposal is None
    finally:
        db.close()


def test_cash_for_an_unprocessed_event_is_refused_with_a_reason():
    db = _fresh_db()
    try:
        _seed_event(db, ledger=False)
        result = get_tool("record_cash").run(
            db=db, event="Pikesville Farmers Market", amount=7.0)
        assert not result.ok
        assert "hasn't been processed" in result.error
    finally:
        db.close()


def test_applying_a_proposal_goes_through_the_shared_ledger_path():
    """Confirming does not write here — it calls the same endpoint Record
    Payments and the cash automation use, so the override, the recompute, the
    audit line and the min-guarantee settlement keep one implementation."""
    db = _fresh_db()
    try:
        _seed_event(db, cash=12.0)
        proposal = get_tool("record_cash").run(
            db=db, event="Pikesville Farmers Market", amount=63.0).proposal

        outcome = apply_proposal(db, proposal, by="admin@example.com")
        assert outcome["ok"], outcome

        entry = db.query(FinancialEntry).one()
        assert entry.cash_collected == 63.0
        # Written through the override machinery, not assigned directly — which
        # is what makes it survive the next pipeline run.
        assert (entry.overrides or {}).get("cash_collected") == 63.0
    finally:
        db.close()


def test_an_unknown_proposal_kind_is_refused_rather_than_half_applied():
    db = _fresh_db()
    try:
        outcome = apply_proposal(db, {"kind": "delete_everything"}, by="x")
        assert not outcome["ok"]
        assert "delete_everything" in outcome["summary"]
    finally:
        db.close()
