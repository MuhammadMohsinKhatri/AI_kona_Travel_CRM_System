"""The tool registry — the only thing that knows what Aimee can do.

Every tool is a self-contained module under ``app/aimee/tools/`` that registers
itself here. Nothing else imports the list, which is what makes the modules
independent: editing ``fleet.py`` cannot break ``reports.py``, because they share
this interface and nothing else. That is where the isolation comes from — not
from splitting the agent up, which would buy the same separation at the cost of
a model round-trip per hop.

**Reads run. Writes propose.**

A tool declares ``kind="read"`` or ``kind="write"``. A read executes and its
result goes back to the model. A write does NOT write: it returns a description
of the change, which the chat renders as a card with Apply and Cancel. The same
review-then-apply shape as Record Payments, for the same reason — a model's
confident mistake becomes a two-second cancel instead of a wrong figure in the
ledger, discovered whenever somebody next looks.

**Nothing raises into the agent loop.** Every tool returns a structured result,
including its failures. A dead Samsara key means Aimee says "I can't reach the
trucks right now" and carries on answering everything else; it does not mean a
broken conversation. Being told what failed also stops the model inventing a
truck location to fill the silence, which is the failure mode that matters.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

ToolKind = Literal["read", "write"]


@dataclass
class ToolResult:
    """What a tool hands back. Never an exception."""

    ok: bool
    data: Any = None
    error: str = ""
    # Whether trying again might work — a timeout, yes; a bad event id, no.
    retryable: bool = False
    # Write tools only: the change awaiting confirmation.
    proposal: Optional[dict[str, Any]] = None
    # Rendered by the CHAT, never shown to the model.
    #
    # Anything a language model is handed, it will paraphrase. Given a Street
    # View URL it produced a plausible-looking maps.googleapis.com link with
    # `key=YOUR_API_KEY` in it — an invented URL that renders nothing and
    # would have leaked the key's location had it guessed better. A URL is not
    # prose and there is no reason for it to pass through a model at all, so
    # this half goes straight to the UI and the model is simply told the image
    # is already on screen.
    display: Optional[dict[str, Any]] = None

    def for_model(self, kind: ToolKind = "read") -> dict[str, Any]:
        """The shape the model sees. Deliberately small and literal.

        A FAILED WRITE says so in as many words. Asked to record cash for a
        name matching five events, record_cash correctly refused and listed
        them — and the model answered "I'll record $7 for the 2026-07-28 one.
        Confirm below." There was nothing below: no proposal was built, so no
        card was rendered, and the user was left waiting on a button that did
        not exist for a change that was never staged.

        A bare error string leaves the model free to resolve the ambiguity on
        the user's behalf and describe a confirmation step it has no way to
        create. Saying `no_change_staged` and naming the next move removes the
        room to invent one — the same reasoning as withholding the Street View
        URL: do not hand a model something it can narrate its way past.
        """
        if not self.ok:
            failed: dict[str, Any] = {
                "ok": False, "error": self.error, "retryable": self.retryable,
            }
            if kind == "write":
                failed["no_change_staged"] = True
                failed["next_step"] = (
                    "NOTHING was staged and there is NO confirmation card on "
                    "screen. Do not say 'confirm below', do not claim you will "
                    "record anything, and do not choose for the user. Report "
                    "what the error says and ask them to pick."
                )
            return failed
        if self.proposal is not None:
            return {"ok": True, "awaiting_confirmation": True,
                    "proposed": self.proposal.get("summary", "")}
        return {"ok": True, "data": self.data}

    def for_record(self, kind: ToolKind = "read") -> dict[str, Any]:
        """What is stored on the message — the model's view plus the UI's.

        Kept under a underscored key so it cannot collide with a tool's own
        data, and stripped before anything is sent upstream.
        """
        record = self.for_model(kind)
        if self.display is not None:
            record = {**record, "_display": self.display}
        return record


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]          # JSON Schema
    kind: ToolKind
    run: Callable[..., ToolResult]
    # Shown in the UI when this tool is running, e.g. "Checking truck location".
    running_label: str = ""

    def schema(self) -> dict[str, Any]:
        """OpenAI tool-calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


_REGISTRY: dict[str, Tool] = {}


def tool(
    *,
    name: str,
    description: str,
    parameters: dict[str, Any],
    kind: ToolKind = "read",
    running_label: str = "",
) -> Callable:
    """Register a tool.

    The wrapper is what guarantees the loop never sees an exception: whatever a
    tool does wrong — a timeout, a missing key, a shape nobody expected — comes
    back as ToolResult(ok=False) with the reason attached.
    """

    def decorate(fn: Callable[..., ToolResult]) -> Callable[..., ToolResult]:
        def guarded(db: Session, **kwargs: Any) -> ToolResult:
            try:
                return fn(db=db, **kwargs)
            except TypeError as e:
                # The model invented an argument, or omitted a required one.
                # Its own mistake, and one it can correct if told plainly.
                return ToolResult(
                    ok=False,
                    error=f"{name} was called with arguments it doesn't accept: {e}",
                )
            except Exception as e:  # noqa: BLE001 — the whole point of this layer
                log.exception("tool %s failed", name)
                return ToolResult(
                    ok=False,
                    error=f"{name} failed: {e}",
                    retryable=True,
                )

        _REGISTRY[name] = Tool(
            name=name,
            description=description,
            parameters=parameters,
            kind=kind,
            run=guarded,
            running_label=running_label or f"Running {name}",
        )
        return guarded

    return decorate


def all_tools() -> list[Tool]:
    # Import for side effects: each module registers on import. Done here rather
    # than at module scope so a broken tool file fails loudly at startup instead
    # of silently leaving a capability missing.
    from app.aimee.tools import (calendar, finance, fleet,  # noqa: F401
                                 labor, maps, reports)

    return list(_REGISTRY.values())


def get_tool(name: str) -> Optional[Tool]:
    all_tools()
    return _REGISTRY.get(name)


def schemas() -> list[dict[str, Any]]:
    return [t.schema() for t in all_tools()]
