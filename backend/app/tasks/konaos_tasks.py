"""Scheduled KonaOS session maintenance.

The KonaOS session key rotates roughly every 15-30 days. This task runs daily:
  * probes the current key against KonaOS,
  * if dead (or older than PROACTIVE_REFRESH_DAYS) attempts a refresh —
    first re-reading the persisted cache, then a real email/password login,
  * if refresh fails, sends a Telegram notification telling the operator to
    paste a fresh key (POST /api/konaos/session or the API Explorer tab).
"""
from __future__ import annotations

import asyncio

from app.tasks.celery_app import celery

PROACTIVE_REFRESH_DAYS = 13.0  # refresh before the ~15-day rotation bites


@celery.task(name="app.tasks.konaos_tasks.maintain_konaos_session")
def maintain_konaos_session() -> dict:
    from app.integrations import factory
    from app.konaos.client import KonaosClient

    async def check() -> dict:
        kc = KonaosClient()
        alive = await kc.probe_session()
        age = kc.session_age_days()

        needs_refresh = (not alive) or (age is not None and age >= PROACTIVE_REFRESH_DAYS)
        if not needs_refresh:
            await kc.close()
            return {"status": "ok", "valid": True, "age_days": age}

        try:
            await kc._login()
            alive = await kc.probe_session()
        except Exception as e:  # noqa: BLE001
            await kc.close()
            return {"status": "refresh_failed", "valid": False, "error": str(e)}
        await kc.close()
        return {"status": "refreshed", "valid": alive, "age_days": kc.session_age_days()}

    result = asyncio.run(check())

    if not result.get("valid"):
        _raise_session_alert(result.get("error", "the key stopped working"))
    else:
        _resolve_session_alerts()
    return result


SESSION_ISSUE = "KonaOS connection needs a new session key"


def _raise_session_alert(reason: str) -> None:
    """Put the dead session key on Needs Attention and push it to Telegram.

    Goes through the same Alert table as everything else so there is one place
    to look — a system problem that only ever appeared in a chat message is a
    problem nobody finds later. Idempotent: while one is unresolved, a daily
    re-check won't stack duplicates.
    """
    from app.core import notify
    from app.db.base import SessionLocal
    from app.models import Alert

    db = SessionLocal()
    try:
        existing = (
            db.query(Alert)
            .filter(Alert.issue == SESSION_ISSUE, Alert.resolved.is_(False))
            .first()
        )
        if existing is not None:
            return
        alert = Alert(
            event_id=None,  # system-level: no event to attach to
            severity="CRITICAL",
            source="session",
            issue=SESSION_ISSUE,
            action=(
                "The automation can't reach KonaOS, so no events can be read and no "
                "invoices created until this is fixed. Open the API Explorer page, go "
                f"to KonaOS Session, and paste a fresh session key. (Reason: {reason})"
            ),
        )
        db.add(alert)
        db.flush()
        notify.notify_alert(db, alert)
        db.commit()
    finally:
        db.close()


def _resolve_session_alerts() -> None:
    """Close out session alerts once the key is working again."""
    from datetime import datetime, timezone

    from app.db.base import SessionLocal
    from app.models import Alert

    db = SessionLocal()
    try:
        stale = (
            db.query(Alert)
            .filter(Alert.issue == SESSION_ISSUE, Alert.resolved.is_(False))
            .all()
        )
        for alert in stale:
            alert.resolved = True
            alert.resolved_at = datetime.now(timezone.utc)
        if stale:
            db.commit()
    finally:
        db.close()
