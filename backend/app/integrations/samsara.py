"""Samsara — where the trucks are and how much fuel they have.

Read-only, deliberately. Samsara can dispatch and reassign; nothing here does,
because the only questions asked of it are "where is the truck" and "does it
need fuel before tomorrow". A client that cannot write cannot write by mistake.

Two endpoints, and they answer different questions despite the similar names:

  * ``/fleet/vehicles`` is the ROSTER — ids and names, which is how a human's
    "the Catonsville truck" becomes an id. It changes when a vehicle is bought
    or sold, so it is cached for the process lifetime rather than fetched per
    question.
  * ``/fleet/vehicles/locations`` and ``/fleet/vehicles/stats`` are LIVE. Never
    cached: a location from ten minutes ago is not a location, and answering
    "where is it" with a stale fix is worse than saying the truck is offline.

Everything returns ``(value, error)`` rather than raising. A dead token has to
reach the user as "I can't see the trucks right now", not as a broken
conversation — see ``app/aimee/registry.py``.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.config import settings

log = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 12
# Samsara pages at 512; a franchise fleet is a handful of trucks, so one page is
# always the lot. Asked for explicitly so a silent default change can't truncate.
PAGE_LIMIT = 512


class SamsaraError(RuntimeError):
    pass


def configured() -> bool:
    return bool(settings.samsara_api_token)


def _get(path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    if not configured():
        raise SamsaraError(
            "No Samsara API token configured (SAMSARA_API_TOKEN)."
        )
    url = f"{settings.samsara_api_base.rstrip('/')}{path}"
    try:
        resp = httpx.get(
            url,
            headers={
                "Authorization": f"Bearer {settings.samsara_api_token}",
                "Accept": "application/json",
            },
            params={"limit": PAGE_LIMIT, **(params or {})},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json() or {}
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            detail = str((e.response.json() or {}).get("message") or "")
        except Exception:  # noqa: BLE001 — the status alone is still useful
            pass
        if e.response.status_code in (401, 403):
            raise SamsaraError(
                "Samsara rejected the API token — it may have been revoked or "
                "lack fleet read scope."
            ) from e
        raise SamsaraError(
            f"Samsara returned {e.response.status_code}"
            + (f": {detail}" if detail else "")
        ) from e
    except (httpx.HTTPError, ValueError) as e:
        raise SamsaraError(f"Couldn't reach Samsara: {e}") from e


_roster: Optional[list[dict[str, Any]]] = None


def vehicles(refresh: bool = False) -> list[dict[str, Any]]:
    """The fleet roster: ``[{"id", "name"}]``.

    Cached because it is answered from the same list on every fleet question,
    and a truck does not change its name mid-conversation. ``refresh=True``
    when a name genuinely isn't found, so a newly-added vehicle doesn't need a
    redeploy to become answerable.
    """
    global _roster
    if _roster is not None and not refresh:
        return _roster
    data = _get("/fleet/vehicles")
    _roster = [
        {"id": str(v.get("id") or ""), "name": str(v.get("name") or "").strip()}
        for v in (data.get("data") or [])
        if v.get("id")
    ]
    return _roster


def find_vehicle(query: str) -> Optional[dict[str, Any]]:
    """One truck from however a person referred to it.

    Exact id, then exact name, then a unique substring. A substring matching
    two trucks returns None rather than the first — "the Kona truck" when there
    are three is a question to ask back, not a coin to flip.
    """
    q = (query or "").strip().lower()
    if not q:
        return None
    fleet = vehicles()
    for v in fleet:
        if v["id"].lower() == q:
            return v
    exact = [v for v in fleet if v["name"].lower() == q]
    if len(exact) == 1:
        return exact[0]
    partial = [v for v in fleet if q in v["name"].lower()]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        # A name we've never seen may be a truck added since this process
        # started; re-pull once before giving up.
        for v in vehicles(refresh=True):
            if q in v["name"].lower() or v["id"].lower() == q:
                return v
    return None


def locations() -> list[dict[str, Any]]:
    """Live position for every vehicle.

    ``[{"id", "name", "latitude", "longitude", "address", "speed_mph", "at"}]``
    — ``address`` is Samsara's own reverse geocode where it has one, which
    saves a Google call for the common case.
    """
    data = _get("/fleet/vehicles/locations")
    out: list[dict[str, Any]] = []
    for row in data.get("data") or []:
        loc = row.get("location") or {}
        rev = loc.get("reverseGeo") or {}
        out.append({
            "id": str(row.get("id") or ""),
            "name": str(row.get("name") or "").strip(),
            "latitude": loc.get("latitude"),
            "longitude": loc.get("longitude"),
            "address": str(rev.get("formattedLocation") or "").strip(),
            "speed_mph": loc.get("speed"),
            "at": str(loc.get("time") or ""),
        })
    return out


def fuel_levels() -> list[dict[str, Any]]:
    """Fuel percentage per vehicle, newest reading.

    ``fuelPercents`` is the series Samsara exposes for this; a vehicle that has
    never reported one (no compatible sender) comes back with ``percent: None``
    rather than 0, because "unknown" and "empty" prompt opposite actions.
    """
    data = _get("/fleet/vehicles/stats", {"types": "fuelPercents"})
    out: list[dict[str, Any]] = []
    for row in data.get("data") or []:
        fp = row.get("fuelPercent") or row.get("fuelPercents") or {}
        if isinstance(fp, list):
            fp = fp[-1] if fp else {}
        value = fp.get("value") if isinstance(fp, dict) else None
        out.append({
            "id": str(row.get("id") or ""),
            "name": str(row.get("name") or "").strip(),
            "percent": value,
            "at": str((fp or {}).get("time") or ""),
        })
    return out
