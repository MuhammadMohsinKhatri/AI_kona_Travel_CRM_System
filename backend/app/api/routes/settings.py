"""User-editable runtime settings (currently Telegram alert delivery)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core import notify
from app.db.base import get_db
from app.models import User

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Shown instead of the real token once one is saved. The token is a live
# credential — anyone who can read it controls the bot — so it is never sent
# back to the browser after being stored.
MASK = "••••••••"


class TelegramSettings(BaseModel):
    enabled: bool = False
    chat_ids: list[str] = Field(default_factory=list)
    dashboard_url: str = ""
    # Omit to keep the stored token; send "" to clear it.
    bot_token: Optional[str] = None


def _public(cfg: dict) -> dict:
    """Config safe to hand back to the browser — token masked, never echoed."""
    return {
        "enabled": cfg["enabled"],
        "chat_ids": cfg["chat_ids"],
        "dashboard_url": cfg["dashboard_url"],
        "bot_token_set": bool(cfg["bot_token"]),
        "bot_token": MASK if cfg["bot_token"] else "",
        "configured": bool(cfg["enabled"] and cfg["bot_token"] and cfg["chat_ids"]),
    }


@router.get("/telegram")
def get_telegram(
    db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> dict:
    return _public(notify.get_config(db))


@router.put("/telegram")
def update_telegram(
    body: TelegramSettings,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    """Save Telegram settings.

    The token is write-only: leaving it out (or sending the mask back
    unchanged) keeps whatever is stored, so a user can edit chat ids without
    having to re-enter the token — and without the browser ever holding it.
    """
    patch: dict = {
        "enabled": body.enabled,
        # De-duplicated, order preserved, blanks dropped.
        "chat_ids": list(dict.fromkeys(c.strip() for c in body.chat_ids if c.strip())),
        "dashboard_url": body.dashboard_url.strip().rstrip("/"),
    }
    if body.bot_token is not None and body.bot_token != MASK:
        patch["bot_token"] = _clean_token(body.bot_token)
    return _public(notify.save_config(db, patch))


@router.post("/telegram/test")
def test_telegram(
    db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> dict:
    """Send a test message to every configured chat and report what happened.

    Errors come back as data, not as a failed request: "chat not found" is
    information the user needs, not a 500.
    """
    result = notify.send_test(db)
    if result.get("skipped"):
        return {
            "ok": False,
            "detail": "Turn Telegram on, add a bot token, and add at least one chat id first.",
            **result,
        }
    if result["sent"] and not result["errors"]:
        return {"ok": True, "detail": f"Sent to {result['sent']} chat(s).", **result}
    if result["sent"]:
        return {
            "ok": True,
            "detail": f"Sent to {result['sent']} chat(s), {result['failed']} failed.",
            **result,
        }
    return {"ok": False, "detail": "Couldn't send to any chat.", **result}


def _clean_token(raw: str) -> str:
    """Accept a bare token or a pasted API URL.

    People copy the whole https://api.telegram.org/bot<TOKEN>/sendMessage URL
    out of their browser, so pull the token out rather than rejecting it.
    """
    token = raw.strip()
    if "api.telegram.org" in token:
        after = token.split("/bot", 1)[-1]
        token = after.split("/", 1)[0]
    return token.strip()
