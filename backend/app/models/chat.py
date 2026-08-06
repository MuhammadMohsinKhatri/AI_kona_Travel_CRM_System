from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base


class Conversation(Base):
    """One thread with Aimee.

    Scoped to a user rather than shared: two people asking about the same truck
    should not see each other's half-finished questions, and a proposed write
    belongs to whoever was asked to confirm it.
    """

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # First line of the opening question, for the sidebar. Written once so
    # renaming a thread later is possible without recomputing it.
    title: Mapped[str] = mapped_column(String(200), default="")

    # A running précis of turns that have fallen out of the live window. This is
    # what keeps a long conversation coherent without resending all of it — see
    # app/aimee/history.py.
    summary: Mapped[str] = mapped_column(String, default="")

    # Cost of this thread alone, in dollars. Accumulated per message so the
    # header can show it without summing rows on every render.
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        index=True,
    )

    messages = relationship(
        "ChatMessage", back_populates="conversation",
        cascade="all, delete-orphan", order_by="ChatMessage.id",
    )


class ChatMessage(Base):
    """One turn. User, assistant, or the record of a tool that ran.

    Tool results are rows here rather than text appended to the assistant's
    message, for two reasons: a 40 KB sales report must not be replayed into
    every subsequent prompt, and the screen needs to render "checked the truck's
    location" as a step rather than as prose.
    """

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )

    # user | assistant | tool
    role: Mapped[str] = mapped_column(String(16), index=True)
    content: Mapped[str] = mapped_column(String, default="")

    # Tool rows only: which tool, what it was given, what came back.
    tool_name: Mapped[Optional[str]] = mapped_column(String(64))
    tool_args: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    tool_result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    tool_ok: Mapped[Optional[bool]] = mapped_column()

    # A write a person has been asked to confirm. Held here, not applied, until
    # they press Apply — see app/aimee/registry.py on why writes propose rather
    # than act.
    proposal: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    # pending | applied | cancelled — null for anything that isn't a proposal.
    proposal_status: Mapped[Optional[str]] = mapped_column(String(16), index=True)

    # What this turn cost. Stored per message so the conversation total is a
    # sum of facts rather than an estimate, and so an expensive turn is
    # attributable to the question that caused it.
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    model: Mapped[str] = mapped_column(String(64), default="")

    # Voice and image arrive as files; the transcript keeps a reference rather
    # than the bytes.
    attachment_kind: Mapped[Optional[str]] = mapped_column(String(16))
    attachment_name: Mapped[Optional[str]] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    conversation = relationship("Conversation", back_populates="messages")
