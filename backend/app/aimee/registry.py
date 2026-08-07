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

    def for_model(self) -> dict[str, Any]:
        """The shape the model sees. Deliberately small and literal."""
        if not self.ok:
            return {"ok": False, "error": self.error, "retryable": self.retryable}
        if self.proposal is not None:
            return {"ok": True, "awaiting_confirmation": True,
                    "proposed": self.proposal.get("summary", "")}
        return {"ok": True, "data": self.data}

    def for_record(self) -> dict[str, Any]:
        """What is stored on the message — the model's view plus the UI's.

        Kept under a underscored key so it cannot collide with a tool's own
        data, and stripped before anything is sent upstream.
        """
        record = self.for_model()
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
