"""Aimee — the loop.

One agent, one model, the tools from the registry. Deliberately not a graph of
sub-agents: twelve tools is comfortable for one model, and every extra hop costs
a round-trip, a billed routing call, and a failure mode harder to diagnose than
a tool returning an error. The isolation that matters lives in the tool modules,
which share nothing but the registry interface.

The loop is the ordinary one — call the model, run any tools it asked for, feed
the results back, repeat until it answers in prose — with three constraints:

  * a hard cap on rounds, so a model that keeps calling tools cannot bill
    forever;
  * writes never execute here, they come back as proposals; and
  * every turn's tokens and cost are recorded on the message, so the running
    total is a sum of facts rather than an estimate.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.aimee.registry import get_tool, schemas
from app.config import settings
from app.models import ChatMessage, Conversation

log = logging.getLogger(__name__)

MODEL = "gpt-4o"

# Prices per million tokens. Kept beside the model name so the two are changed
# together — a model swap with stale pricing reports a confident wrong number.
COST_PER_MTOK = {"gpt-4o": {"in": 2.50, "out": 10.00}}

# Enough for a question needing several lookups; short of a loop that never
# converges. Hit, it answers with what it has rather than stopping dead.
MAX_ROUNDS = 6

# How many past turns go to the model verbatim. Older ones are represented by
# the conversation summary instead — see history.py.
LIVE_WINDOW = 20


SYSTEM_PROMPT = """You are Aimee, the assistant for a Kona Ice and Travelin' Tom's
franchise in Baltimore. You help the office with events, trucks, staff and money.

Today is {today}.

HOW TO ANSWER
- Lead with the answer. Context after, if it helps.
- Be concrete: real names, real dates, real figures. Never "several events" when
  you can say "four".
- Format for a chat window: short paragraphs, markdown tables for more than two
  rows of figures, bold for the number that matters.
- Money as $1,234.56. Dates as "Mon 29 Jul" in prose, YYYY-MM-DD in tables.

USING TOOLS
- Call them. Do not narrate that you are about to, and do not ask permission to
  look something up — looking is free.
- If a tool fails, say what is unavailable in one line and answer what you can
  from the rest. Never fill the gap with a plausible guess: a made-up truck
  location is worse than "I can't reach the trucks right now".
- If a question needs data no tool provides, say so plainly rather than
  reasoning from what you would expect to be true.

RECORDING THINGS
- Tools that change data do not change it when you call them. They prepare the
  change and the person confirms it on screen.
- So say "I'll record $63 cash for Arbutus — confirm below", never "I've
  recorded it". Telling somebody a thing is done when it is waiting on them is
  the one mistake here that costs trust.

WHAT YOU DO NOT KNOW
- You cannot see the dashboard, send email or text anyone.
- You have no memory beyond this conversation.
- If unsure which event somebody means, ask. Two events at the same school in
  one week is normal, and posting cash to the wrong one is a real cost.
"""


@dataclass
class Turn:
    """What one exchange produced, for the caller to persist and render."""

    reply: str = ""
    tool_messages: list[ChatMessage] = field(default_factory=list)
    proposal: Optional[dict[str, Any]] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    model: str = MODEL
    error: str = ""


def _client():
    from openai import OpenAI

    from app.integrations.live import _clean_api_key

    return OpenAI(api_key=_clean_api_key(settings.openai_api_key))


def _cost(model: str, prompt: int, completion: int) -> float:
    price = COST_PER_MTOK.get(model) or COST_PER_MTOK[MODEL]
    return round(prompt / 1e6 * price["in"] + completion / 1e6 * price["out"], 6)


def _history(db: Session, conversation: Conversation) -> list[dict[str, Any]]:
    """The conversation as the model should see it.

    Tool rows become the compact result the model was given, not the full
    payload we stored — a sales report is worth its top rows once, not its
    entire body on every subsequent turn.
    """
    rows = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conversation.id)
        .order_by(ChatMessage.id.desc())
        .limit(LIVE_WINDOW)
        .all()
    )
    rows.reverse()

    out: list[dict[str, Any]] = []
    if conversation.summary:
        out.append({
            "role": "system",
            "content": f"Earlier in this conversation: {conversation.summary}",
        })
    for m in rows:
        if m.role == "tool":
            out.append({
                "role": "assistant",
                "content": (
                    f"[used {m.tool_name}: "
                    f"{'ok' if m.tool_ok else 'failed'}]"
                ),
            })
            continue
        if m.content:
            out.append({"role": m.role, "content": m.content})
    return out


def run_turn(
    db: Session,
    conversation: Conversation,
    user_text: str,
    *,
    image_data_url: str = "",
) -> Turn:
    """One user message in, one answer out, with any tool work in between."""
    if settings.openai_provider != "live":
        return Turn(error="Aimee needs OPENAI_PROVIDER=live.")

    turn = Turn()
    tools = schemas()

    content: Any = user_text
    if image_data_url:
        # Vision turns take the list form; text-only stays a plain string so the
        # ordinary case sends the smaller payload.
        content = [
            {"type": "text", "text": user_text or "What is in this image?"},
            {"type": "image_url", "image_url": {"url": image_data_url}},
        ]

    messages: list[dict[str, Any]] = [
        {"role": "system",
         "content": SYSTEM_PROMPT.format(today=datetime.now().strftime("%A %d %B %Y"))},
        *_history(db, conversation),
        {"role": "user", "content": content},
    ]

    client = _client()

    for _round in range(MAX_ROUNDS):
        try:
            response = client.chat.completions.create(
                model=MODEL, messages=messages, tools=tools, tool_choice="auto",
            )
        except Exception as e:  # noqa: BLE001
            log.exception("aimee model call failed")
            turn.error = f"Couldn't reach the model: {e}"
            return turn

        usage = getattr(response, "usage", None)
        if usage:
            turn.prompt_tokens += usage.prompt_tokens or 0
            turn.completion_tokens += usage.completion_tokens or 0

        choice = response.choices[0].message
        calls = getattr(choice, "tool_calls", None) or []

        if not calls:
            turn.reply = (choice.content or "").strip()
            break

        messages.append({
            "role": "assistant",
            "content": choice.content or "",
            "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in calls
            ],
        })

        for call in calls:
            name = call.function.name
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            spec = get_tool(name)
            if spec is None:
                result_for_model: dict[str, Any] = {
                    "ok": False, "error": f"No tool called {name}."}
                record = ChatMessage(
                    conversation_id=conversation.id, role="tool", tool_name=name,
                    tool_args=args, tool_result=result_for_model, tool_ok=False,
                )
            else:
                result = spec.run(db=db, **args)
                result_for_model = result.for_model()
                record = ChatMessage(
                    conversation_id=conversation.id, role="tool", tool_name=name,
                    tool_args=args, tool_result=result_for_model, tool_ok=result.ok,
                )
                # A write stops here. The model is told it is awaiting
                # confirmation so it describes the change rather than claiming
                # to have made it.
                if result.proposal is not None:
                    record.proposal = result.proposal
                    record.proposal_status = "pending"
                    turn.proposal = result.proposal

            db.add(record)
            db.flush()
            turn.tool_messages.append(record)

            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result_for_model)[:6000],
            })
    else:
        # Ran out of rounds still calling tools. Answer with what is in hand
        # rather than leaving the question hanging.
        turn.reply = (
            "I gathered what I could but couldn't finish working through that. "
            "Try asking for one thing at a time."
        )

    turn.cost_usd = _cost(MODEL, turn.prompt_tokens, turn.completion_tokens)
    return turn
