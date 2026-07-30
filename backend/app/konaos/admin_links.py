"""Deep links into the KonaOS admin UI.

Separate from ``client``/``router`` on purpose: these are pure string builders
with no session, no HTTP and no FastAPI, so anything (the event model, a
router, a notification email) can import them without dragging the CRM client
into scope.

The admin app is an Angular hash-router, so the event id rides in the fragment
query — ``/#/franchise/events/edit-event?id=<id>&eventType=list``. That is the
exact URL the admin's own Events grid navigates to, captured from devtools;
dropping ``eventType=list`` loads the shell without selecting the event.
"""
from __future__ import annotations

import os

ADMIN_BASE_URL = os.getenv("KONAOS_ADMIN_BASE_URL", "https://admin.konaos.com").rstrip("/")


def event_admin_url(crm_event_id: str | None) -> str | None:
    """Link to one event's edit screen in KonaOS, or None if there's no id.

    Only meaningful for ids that came FROM KonaOS — the mock provider's
    "EVT-1001"-style ids would produce a link to nothing, so callers gate on
    the configured CRM provider (see Event.konaos_url).
    """
    eid = str(crm_event_id or "").strip()
    if not eid:
        return None
    return f"{ADMIN_BASE_URL}/#/franchise/events/edit-event?id={eid}&eventType=list"
