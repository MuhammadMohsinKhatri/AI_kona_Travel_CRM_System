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
        notifier = factory.get_notifier()
        notifier.send(
            "⚠️ *KonaOS session key needs attention*\n\n"
            "The session key is stale and automatic refresh failed.\n"
            f"Reason: {result.get('error', 'probe failed')}\n\n"
            "👉 Grab a fresh `jsessionId` from admin.konaos.com devtools and "
            "paste it in the dashboard (API Explorer → KonaOS Session) or:\n"
            "`POST /api/konaos/session {\"session_key\": \"...\"}`"
        )
    return result
