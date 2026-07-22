"""Telegram delivery for alerts, configured from the Settings page.

Two rules shape this module:

1. **Never let notification failure break the thing that raised the alert.**
   A pipeline run that computed invoices correctly must not be marked failed
   because Telegram was unreachable. Every send is best-effort and returns a
   result rather than raising.

2. **Unconfigured is a normal state, not an error.** With no bot token the
   alert still lands on the Needs Attention page; the push is simply skipped.
   That's the documented behaviour, not a silent failure.

Config lives in the database (``AppSetting``) rather than the environment so
a user can rotate the bot token or add a recipient without a redeploy.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from app.models import TELEGRAM_KEY, Alert, AppSetting

TELEGRAM_API = "https://api.telegram.org"
SEND_TIMEOUT_SECONDS = 10

SEVERITY_ICON = {
    "CRITICAL": "🚨",
    "HIGH": "🔴",
    "MEDIUM": "🟠",
    "LOW": "🔵",
}


def get_config(db: Session) -> dict[str, Any]:
    """Current Telegram settings, with defaults for a fresh install."""
    row = db.get(AppSetting, TELEGRAM_KEY)
    value = dict(row.value or {}) if row else {}
    return {
        "enabled": bool(value.get("enabled", False)),
        "bot_token": value.get("bot_token", "") or "",
        "chat_ids": [str(c) for c in (value.get("chat_ids") or []) if str(c).strip()],
        "dashboard_url": (value.get("dashboard_url") or "").rstrip("/"),
    }


def save_config(db: Session, patch: dict[str, Any]) -> dict[str, Any]:
    row = db.get(AppSetting, TELEGRAM_KEY)
    if row is None:
        row = AppSetting(key=TELEGRAM_KEY, value={})
        db.add(row)
    # Reassign rather than mutate: SQLAlchemy doesn't track in-place changes to
    # a JSON dict, so mutating would look saved and silently vanish.
    row.value = {**(row.value or {}), **patch}
    db.commit()
    return get_config(db)


def is_configured(db: Session) -> bool:
    cfg = get_config(db)
    return bool(cfg["enabled"] and cfg["bot_token"] and cfg["chat_ids"])


def send_message(db: Session, text: str) -> dict[str, Any]:
    """Send one message to every configured chat.

    Returns ``{"sent": n, "failed": n, "skipped": bool, "errors": [...]}``.
    Never raises — callers are pipeline steps and scheduled tasks whose real
    job must not fail over a notification.
    """
    cfg = get_config(db)
    if not (cfg["enabled"] and cfg["bot_token"] and cfg["chat_ids"]):
        return {
            "sent": 0, "failed": 0, "skipped": True, "errors": [],
            "reason": "Telegram is not set up — the alert is on the Needs Attention page only",
        }

    sent, errors = 0, []
    url = f"{TELEGRAM_API}/bot{cfg['bot_token']}/sendMessage"
    for chat_id in cfg["chat_ids"]:
        try:
            resp = httpx.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=SEND_TIMEOUT_SECONDS,
            )
            if resp.status_code == 200:
                sent += 1
            else:
                # Telegram puts the useful part in `description`
                detail = ""
                try:
                    detail = resp.json().get("description", "")
                except Exception:  # noqa: BLE001
                    detail = resp.text[:120]
                errors.append(f"chat {chat_id}: {resp.status_code} {detail}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"chat {chat_id}: {type(exc).__name__} {exc}")

    return {
        "sent": sent,
        "failed": len(errors),
        "skipped": False,
        "errors": errors,
    }


def alert_url(db: Session, alert_id: int) -> str:
    """Deep link to the alert's own page, or "" if no dashboard URL is set."""
    base = get_config(db)["dashboard_url"]
    return f"{base}/alerts/{alert_id}" if base else ""


def format_alert(db: Session, alert: Alert, event_name: str = "") -> str:
    """Short, actionable Telegram message: what, which event, what to do, link.

    Deliberately brief. The detail lives on the alert page; a wall of text in
    a chat window gets skimmed and ignored.
    """
    icon = SEVERITY_ICON.get(alert.severity, "🔔")
    lines = [f"{icon} <b>{_esc(alert.severity)}</b> — {_esc(alert.issue)}"]
    if event_name:
        lines.append(f"Event: <b>{_esc(event_name)}</b>")
    if alert.action:
        lines.append(f"Fix: {_esc(alert.action)}")
    link = alert_url(db, alert.id)
    if link:
        lines.append(f'<a href="{_esc(link)}">Open this alert</a>')
    return "\n".join(lines)


def notify_alert(db: Session, alert: Alert, event_name: str = "") -> dict[str, Any]:
    """Push one alert and record on the alert whether it actually went out."""
    result = send_message(db, format_alert(db, alert, event_name))
    alert.notified = result["sent"] > 0
    if result.get("skipped"):
        alert.notify_error = "Telegram not set up"
    elif result["errors"]:
        alert.notify_error = "; ".join(result["errors"])[:255]
    else:
        alert.notify_error = ""
    return result


def send_test(db: Session) -> dict[str, Any]:
    """Used by the Settings page's 'Send test message' button."""
    return send_message(
        db,
        "✅ <b>Test message</b>\nYour Kona Ice dashboard is connected to this chat. "
        "Alerts will arrive here.",
    )


def _esc(text: Any) -> str:
    """Escape the three characters Telegram's HTML parse mode cares about."""
    return (
        str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
