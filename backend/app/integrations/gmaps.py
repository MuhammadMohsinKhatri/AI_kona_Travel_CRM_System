"""Google Maps — geocoding, driving times, routes and Street View.

The API key never leaves the server. Street View and static maps come back as
BYTES through this module and are served from our own ``/api/aimee/media``
endpoint, rather than the browser being handed a maps.googleapis.com URL with
the key in the query string. A dashboard the office shares around is exactly
where a key gets scraped, and Google bills the owner, not the scraper.

Times are computed with ``departure_time`` set, so Google returns
``duration_in_traffic`` where it has it. Without that, a route quoted at 40
minutes at 3pm is quoted the same at 5pm, which is the hour it matters.

Everything raises ``MapsError`` with something a person can act on. The tool
layer turns that into a spoken sentence; nothing here formats for a user.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.config import settings

log = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 15
# Street View at more than this is a bigger download for no more information at
# the size a chat bubble renders it.
STREET_VIEW_SIZE = "640x400"


class MapsError(RuntimeError):
    pass


def configured() -> bool:
    return bool(settings.google_maps_api_key)


def _require_key() -> str:
    if not configured():
        raise MapsError("No Google Maps API key configured (GOOGLE_MAPS_API_KEY).")
    return settings.google_maps_api_key


def _get(path: str, params: dict[str, Any]) -> httpx.Response:
    url = f"{settings.google_maps_api_base.rstrip('/')}{path}"
    try:
        resp = httpx.get(
            url,
            params={**params, "key": _require_key()},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp
    except httpx.HTTPStatusError as e:
        raise MapsError(f"Google Maps returned {e.response.status_code}") from e
    except httpx.HTTPError as e:
        raise MapsError(f"Couldn't reach Google Maps: {e}") from e


def _json(path: str, params: dict[str, Any]) -> dict[str, Any]:
    """A Maps JSON call, with Google's in-body status treated as the error it is.

    Maps answers 200 for "no results" and for "your key is denied"; only the
    ``status`` field distinguishes them. Reading the HTTP code alone would turn
    a billing problem into a confident "no route found".
    """
    data = _get(path, params).json() or {}
    status = str(data.get("status") or "")
    if status in ("OK", "ZERO_RESULTS"):
        return data
    message = str(data.get("error_message") or "").strip()
    if status == "REQUEST_DENIED":
        raise MapsError(
            "Google Maps denied the request — check the API key and that the "
            "Geocoding/Directions APIs are enabled"
            + (f": {message}" if message else "")
        )
    if status == "OVER_QUERY_LIMIT":
        raise MapsError("Google Maps quota exceeded for this key.")
    raise MapsError(f"Google Maps: {status}" + (f" — {message}" if message else ""))


def geocode(address: str) -> dict[str, Any]:
    """``{"address", "latitude", "longitude"}`` for a written address."""
    if not (address or "").strip():
        raise MapsError("No address given to look up.")
    data = _json("/geocode/json", {"address": address})
    results = data.get("results") or []
    if not results:
        raise MapsError(f"Google Maps found no place called \"{address}\".")
    top = results[0]
    loc = ((top.get("geometry") or {}).get("location")) or {}
    return {
        "address": str(top.get("formatted_address") or address),
        "latitude": loc.get("lat"),
        "longitude": loc.get("lng"),
    }


def reverse_geocode(latitude: float, longitude: float) -> str:
    data = _json("/geocode/json", {"latlng": f"{latitude},{longitude}"})
    results = data.get("results") or []
    return str(results[0].get("formatted_address")) if results else ""


def directions(
    origin: str,
    destination: str,
    depart_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """Driving leg between two points.

    ``depart_at`` in the past (or absent) becomes "now": Google rejects a
    historical ``departure_time``, and asking for traffic on a trip that has
    already happened is a question with no useful answer anyway.
    """
    when = depart_at or datetime.now(timezone.utc)
    if when < datetime.now(timezone.utc):
        when = datetime.now(timezone.utc)
    data = _json("/directions/json", {
        "origin": origin,
        "destination": destination,
        "departure_time": int(when.timestamp()),
        "traffic_model": "best_guess",
    })
    routes = data.get("routes") or []
    if not routes:
        raise MapsError(f"No driving route from \"{origin}\" to \"{destination}\".")
    leg = ((routes[0].get("legs") or [{}])[0])
    # duration_in_traffic is absent outside supported regions/times; the plain
    # duration is the honest fallback, and the caller is told which it got.
    in_traffic = leg.get("duration_in_traffic") or {}
    plain = leg.get("duration") or {}
    seconds = in_traffic.get("value") or plain.get("value") or 0
    return {
        "origin": str(leg.get("start_address") or origin),
        "destination": str(leg.get("end_address") or destination),
        "distance_text": str((leg.get("distance") or {}).get("text") or ""),
        "duration_seconds": int(seconds),
        "duration_text": str(in_traffic.get("text") or plain.get("text") or ""),
        "traffic_aware": bool(in_traffic.get("value")),
        "summary": str(routes[0].get("summary") or ""),
    }


def route(stops: list[str], depart_at: Optional[datetime] = None) -> dict[str, Any]:
    """A multi-stop run: first is the origin, last the destination.

    ``optimize:true`` lets Google reorder the middle stops, which is the whole
    value of asking it rather than driving them in the order somebody typed.
    """
    cleaned = [s.strip() for s in (stops or []) if (s or "").strip()]
    if len(cleaned) < 2:
        raise MapsError("A route needs at least a start and an end.")
    origin, destination, middle = cleaned[0], cleaned[-1], cleaned[1:-1]
    params: dict[str, Any] = {"origin": origin, "destination": destination}
    if middle:
        params["waypoints"] = "optimize:true|" + "|".join(middle)
    when = depart_at or datetime.now(timezone.utc)
    params["departure_time"] = int(max(when, datetime.now(timezone.utc)).timestamp())
    data = _json("/directions/json", params)
    routes = data.get("routes") or []
    if not routes:
        raise MapsError("No route covers those stops by road.")
    top = routes[0]
    legs = top.get("legs") or []
    total_seconds = sum(
        (leg.get("duration_in_traffic") or leg.get("duration") or {}).get("value") or 0
        for leg in legs
    )
    total_metres = sum((leg.get("distance") or {}).get("value") or 0 for leg in legs)
    return {
        "order": top.get("waypoint_order") or [],
        "stops": [
            {
                "from": str(leg.get("start_address") or ""),
                "to": str(leg.get("end_address") or ""),
                "duration_text": str((leg.get("duration") or {}).get("text") or ""),
                "distance_text": str((leg.get("distance") or {}).get("text") or ""),
            }
            for leg in legs
        ],
        "total_duration_seconds": int(total_seconds),
        "total_miles": round(total_metres / 1609.344, 1),
    }


def street_view(location: str) -> bytes:
    """A Street View still, as bytes. Empty when Google has no imagery."""
    resp = _get("/streetview", {
        "size": STREET_VIEW_SIZE,
        "location": location,
        "return_error_code": "true",
    })
    content = resp.content or b""
    if not content or not resp.headers.get("content-type", "").startswith("image/"):
        raise MapsError(f"No Street View imagery for \"{location}\".")
    return content
