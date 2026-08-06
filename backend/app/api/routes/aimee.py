"""Aimee's endpoints: conversations, messages, and confirming a proposed write.

Admin-only. Aimee can read every figure the business has and propose changes to
the ledger, so the gate is the same one that guards Settings rather than the
one that guards the dashboard.
"""
from __future__ import annotations

import base64
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.aimee import agent
from app.aimee.registry import all_tools
from app.api.deps import get_current_user
from app.core import ai_budget
from app.db.base import get_db
from app.models import ChatMessage, Conversation, User

router = APIRouter(prefix="/api/aimee", tags=["aimee"])

MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_AUDIO_BYTES = 25 * 1024 * 1024


def admin_only(user: User = Depends(get_current_user)) -> User:
    """Aimee reads everything and can propose ledger changes. Admins only."""
    if not user.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Aimee is available to administrators.",
        )
    return user


def _message_json(m: ChatMessage) -> dict[str, Any]:
    return {
        "id": m.id,
        "role": m.role,
        "content": m.content,
        "tool_name": m.tool_name,
        "tool_ok": m.tool_ok,
        "tool_result": m.tool_result if m.role == "tool" else None,
        "proposal": m.proposal,
        "proposal_status": m.proposal_status,
        "cost_usd": m.cost_usd,
        "attachment_kind": m.attachment_kind,
        "attachment_name": m.attachment_name,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def _conversation_json(c: Conversation, messages: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": c.id,
        "title": c.title or "New chat",
        "cost_usd": round(c.cost_usd or 0.0, 4),
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }
    if messages:
        out["messages"] = [_message_json(m) for m in c.messages]
    return out


def _mine(db: Session, conversation_id: int, user: User) -> Conversation:
    c = db.get(Conversation, conversation_id)
    if c is None or c.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return c


@router.get("/capabilities")
def capabilities(
    db: Session = Depends(get_db), _: User = Depends(admin_only)
) -> dict[str, Any]:
    """What Aimee can do, and what it has cost.

    Drives the empty-state suggestions: rather than facing a blank box and
    guessing what is allowed, someone new sees what is actually available and
    clicks one.
    """
    return {
        "tools": [
            {"name": t.name, "description": t.description, "kind": t.kind}
            for t in all_tools()
        ],
        "budget": ai_budget.status(db),
        "suggestions": [
            {"icon": "📅", "label": "What's on this week?",
             "text": "What events are on this week?"},
            {"icon": "💰", "label": "Sales this month",
             "text": "Show me the sales report for this month"},
            {"icon": "🏆", "label": "Top clients",
             "text": "Who are our top 10 clients this year by revenue?"},
            {"icon": "📊", "label": "Yesterday's events",
             "text": "What events did we have yesterday and what did they invoice?"},
            {"icon": "💵", "label": "Record cash",
             "text": "Record $60 cash for "},
            {"icon": "🗓️", "label": "Tomorrow",
             "text": "What's booked for tomorrow?"},
        ],
    }


@router.get("/conversations")
def list_conversations(
    db: Session = Depends(get_db), user: User = Depends(admin_only)
) -> list[dict[str, Any]]:
    rows = (
        db.query(Conversation)
        .filter(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc())
        .limit(50)
        .all()
    )
    return [_conversation_json(c) for c in rows]


@router.post("/conversations")
def create_conversation(
    db: Session = Depends(get_db), user: User = Depends(admin_only)
) -> dict[str, Any]:
    c = Conversation(user_id=user.id, title="")
    db.add(c)
    db.commit()
    db.refresh(c)
    return _conversation_json(c, messages=True)


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(admin_only),
) -> dict[str, Any]:
    return _conversation_json(_mine(db, conversation_id, user), messages=True)


@router.delete("/conversations/{conversation_id}", status_code=204,
               response_model=None)
def delete_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(admin_only),
) -> None:
    db.delete(_mine(db, conversation_id, user))
    db.commit()


class Ask(BaseModel):
    text: str = Field(default="", max_length=4000)


def _answer(
    db: Session, conversation: Conversation, user: User, text: str,
    *, image_data_url: str = "", attachment: tuple[str, str] | None = None,
) -> dict[str, Any]:
    """Persist the question, run the turn, persist the answer."""
    question = ChatMessage(
        conversation_id=conversation.id, role="user", content=text,
        attachment_kind=attachment[0] if attachment else None,
        attachment_name=attachment[1] if attachment else None,
    )
    db.add(question)
    if not conversation.title:
        conversation.title = (text or "Image").strip()[:120]
    db.flush()

    turn = agent.run_turn(db, conversation, text, image_data_url=image_data_url)

    answer = ChatMessage(
        conversation_id=conversation.id,
        role="assistant",
        content=turn.reply or turn.error,
        prompt_tokens=turn.prompt_tokens,
        completion_tokens=turn.completion_tokens,
        cost_usd=turn.cost_usd,
        model=turn.model,
    )
    db.add(answer)
    conversation.cost_usd = round((conversation.cost_usd or 0.0) + turn.cost_usd, 6)
    db.commit()
    db.refresh(conversation)

    new_messages = [m for m in conversation.messages if m.id >= question.id]
    return {
        "conversation": _conversation_json(conversation),
        "messages": [_message_json(m) for m in new_messages],
        "error": turn.error,
        "budget": ai_budget.status(db),
    }


@router.post("/conversations/{conversation_id}/ask")
def ask(
    conversation_id: int,
    body: Ask,
    db: Session = Depends(get_db),
    user: User = Depends(admin_only),
) -> dict[str, Any]:
    conversation = _mine(db, conversation_id, user)
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="Nothing to ask.")
    return _answer(db, conversation, user, body.text.strip())


# Sync, not async — see app/api/routes/intake.py. The KonaOS adapter drives its
# own event loop, and an async route would run it on the running one.
@router.post("/conversations/{conversation_id}/ask-voice")
def ask_voice(
    conversation_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(admin_only),
) -> dict[str, Any]:
    """Speak a question. Transcribed to English first, whatever was spoken."""
    from app.api.routes.intake import _audio_name, _read_upload
    from app.core.intake_readers import transcribe

    conversation = _mine(db, conversation_id, user)
    audio = _read_upload(file, "recording", MAX_AUDIO_BYTES)
    name = _audio_name(file.filename or "")
    if not name:
        raise HTTPException(
            status_code=400,
            detail=f"\"{file.filename}\" isn't an audio format we can transcribe.",
        )

    text, error = transcribe(audio, name)
    if error:
        raise HTTPException(status_code=502, detail=error)
    if not text.strip():
        raise HTTPException(status_code=400, detail="Nothing was said.")

    return _answer(db, conversation, user, text.strip(),
                   attachment=("voice", file.filename or "recording"))


@router.post("/conversations/{conversation_id}/ask-image")
def ask_image(
    conversation_id: int,
    file: UploadFile = File(...),
    text: str = Form(default=""),
    db: Session = Depends(get_db),
    user: User = Depends(admin_only),
) -> dict[str, Any]:
    """Ask about a photo."""
    from app.api.routes.intake import _read_upload

    conversation = _mine(db, conversation_id, user)
    image = _read_upload(file, "image", MAX_IMAGE_BYTES)
    mime = file.content_type or "image/jpeg"
    data_url = f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"

    return _answer(
        db, conversation, user, text.strip(),
        image_data_url=data_url,
        attachment=("image", file.filename or "image"),
    )


class Decision(BaseModel):
    approve: bool


@router.post("/messages/{message_id}/proposal")
def decide_proposal(
    message_id: int,
    body: Decision,
    db: Session = Depends(get_db),
    user: User = Depends(admin_only),
) -> dict[str, Any]:
    """Apply or cancel a proposed change.

    The proposal stored on the message is the authority, not anything the
    browser sends — the screen chooses yes or no and nothing else.
    """
    message = db.get(ChatMessage, message_id)
    if message is None or not message.proposal:
        raise HTTPException(status_code=404, detail="No such proposal")
    _mine(db, message.conversation_id, user)

    if message.proposal_status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"That change was already {message.proposal_status}.",
        )

    if not body.approve:
        message.proposal_status = "cancelled"
        db.commit()
        return {"ok": True, "status": "cancelled", "summary": "Cancelled."}

    from app.aimee.tools.finance import apply_proposal

    outcome = apply_proposal(db, dict(message.proposal), by=user.email or "aimee")
    message.proposal_status = "applied" if outcome.get("ok") else "pending"
    db.commit()
    return {
        "ok": bool(outcome.get("ok")),
        "status": message.proposal_status,
        "summary": outcome.get("summary", ""),
    }
