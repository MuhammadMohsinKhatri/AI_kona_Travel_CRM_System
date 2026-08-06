"""Driving times, routes, ETAs and Street View.

**Two n8n tools became one here.** "Calculate departure time" and "Calculate
travel and arrival time" called the identical Google Directions and Geocoding
endpoints; they are one question asked from either end — "when must I leave to
be there by 2pm" and "when do I arrive if I leave now". Keeping them apart
meant two prompts, two schemas and two places for the traffic handling to drift.
``get_travel_time`` takes ``arrive_by`` OR ``depart_at`` and answers whichever
was not given.

Street View never hands the browser a Google URL. The tool returns a signed
path on this server which proxies the image, because a maps.googleapis.com URL
carries the API key in its query string, and a dashboard the office shares
around is where keys get scraped and the owner gets the bill.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.aimee.registry import ToolResult, tool
from app.config import settings
from app.integrations import gmaps, samsara


def _parse_when(value: str) -> Optional[datetime]:
    """An ISO timestamp from the model, made timezone-aware.

    A naive value is treated as UTC rather than rejected: the model is told to
    send ISO, and refusing a near-miss helps nobody when the alternative is a
    sensible assumption stated in the reply.
    """
    if not (value or "").strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _mins(seconds: int) -> int:
    return int(round(seconds / 60))


@tool(
    name="get_travel_time",
    kind="read",
    running_label="Working out the drive",
    description=(
        "How long a drive takes, in current traffic — and either when you would "
        "arrive, or when you must leave. Give `arrive_by` to be told the "
        "departure time; give `depart_at` (or neither, meaning now) to be told "
        "the arrival. Omit `origin` to start from the yard."
    ),
    parameters={
        "type": "object",
        "properties": {
            "destination": {"type": "string", "description": "Address or place name"},
            "origin": {
                "type": "string",
                "description": "Start address. Omit to start from the yard.",
            },
            "arrive_by": {
                "type": "string",
                "description": "ISO 8601 time you need to be there by.",
            },
            "depart_at": {
                "type": "string",
                "description": "ISO 8601 time you would set off. Defaults to now.",
            },
        },
        "required": ["destination"],
    },
)
def get_travel_time(
    db: Session,
    destination: str,
    origin: str = "",
    arrive_by: str = "",
    depart_at: str = "",
) -> ToolResult:
    start = (origin or "").strip() or settings.fleet_home_address
    wants_arrival_by = _parse_when(arrive_by)
    leaves_at = _parse_when(depart_at)

    try:
        # Traffic is quoted for the moment of departure. When the question is
        # "when must I leave", that moment is not yet known — so the drive is
        # first priced at the target time, which is far closer to the answer
        # than pricing it at now for a trip happening this evening.
        leg = gmaps.directions(
            start, destination,
            depart_at=leaves_at or wants_arrival_by,
        )
    except gmaps.MapsError as e:
        return ToolResult(ok=False, error=str(e), retryable=True)

    seconds = leg["duration_seconds"]
    out = {
        "origin": leg["origin"],
        "destination": leg["destination"],
        "distance": leg["distance_text"],
        "duration": leg["duration_text"],
        "duration_minutes": _mins(seconds),
        "traffic_aware": leg["traffic_aware"],
        "via": leg["summary"],
    }
    if wants_arrival_by:
        out["arrive_by"] = wants_arrival_by.isoformat()
        out["leave_at"] = (wants_arrival_by - timedelta(seconds=seconds)).isoformat()
        out["answers"] = "when to leave"
    else:
        base = leaves_at or datetime.now(timezone.utc)
        out["depart_at"] = base.isoformat()
        out["arrive_at"] = (base + timedelta(seconds=seconds)).isoformat()
        out["answers"] = "when you arrive"
    return ToolResult(ok=True, data=out)


@tool(
    name="get_truck_eta",
    kind="read",
    running_label="Working out the truck's ETA",
    description=(
        "How long a specific truck needs to reach somewhere, from where it is "
        "RIGHT NOW. Use for 'how far is the Kona truck from the school'. For a "
        "drive that has not started from a truck's live position, use "
        "get_travel_time instead."
    ),
    parameters={
        "type": "object",
        "properties": {
            "truck": {"type": "string", "description": "Truck name or Samsara id"},
            "destination": {"type": "string", "description": "Address or place name"},
        },
        "required": ["truck", "destination"],
    },
)
def get_truck_eta(db: Session, truck: str, destination: str) -> ToolResult:
    try:
        found = samsara.find_vehicle(truck)
        if found is None:
            return ToolResult(ok=False, error=f"No truck matches \"{truck}\".")
        here = [r for r in samsara.locations() if r["id"] == found["id"]]
    except samsara.SamsaraError as e:
        return ToolResult(ok=False, error=str(e), retryable=True)

    if not here or here[0].get("latitude") is None:
        return ToolResult(
            ok=False,
            error=(f"{found['name']} has not reported a position, so there is "
                   "nowhere to measure from."),
        )
    spot = here[0]
    origin = f"{spot['latitude']},{spot['longitude']}"
    try:
        leg = gmaps.directions(origin, destination)
    except gmaps.MapsError as e:
        return ToolResult(ok=False, error=str(e), retryable=True)

    return ToolResult(ok=True, data={
        "truck": found["name"],
        "from": spot.get("address") or origin,
        "position_reported_at": spot.get("at"),
        "destination": leg["destination"],
        "distance": leg["distance_text"],
        "duration": leg["duration_text"],
        "eta": (datetime.now(timezone.utc)
                + timedelta(seconds=leg["duration_seconds"])).isoformat(),
        "traffic_aware": leg["traffic_aware"],
    })


@tool(
    name="plan_truck_route",
    kind="read",
    running_label="Planning the route",
    description=(
        "Order several stops into the quickest run and give the driving time "
        "for each leg. First stop is the start, last is the end; the ones "
        "between are reordered to save time. Use for 'best order for these "
        "three events'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "stops": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Addresses in any order; at least two.",
            },
            "depart_at": {
                "type": "string",
                "description": "ISO 8601 departure time. Defaults to now.",
            },
        },
        "required": ["stops"],
    },
)
def plan_truck_route(db: Session, stops: list, depart_at: str = "") -> ToolResult:
    try:
        planned = gmaps.route([str(s) for s in (stops or [])],
                              depart_at=_parse_when(depart_at))
    except gmaps.MapsError as e:
        return ToolResult(ok=False, error=str(e), retryable=True)
    planned["total_duration_minutes"] = _mins(planned["total_duration_seconds"])
    return ToolResult(ok=True, data=planned)


@tool(
    name="get_street_view",
    kind="read",
    running_label="Finding the street view",
    description=(
        "A Street View photo of an address, so a driver can see what they are "
        "looking for before they get there. Returns an image the chat displays."
    ),
    parameters={
        "type": "object",
        "properties": {
            "location": {"type": "string", "description": "Address or place name"},
        },
        "required": ["location"],
    },
)
def get_street_view(db: Session, location: str) -> ToolResult:
    from app.api.routes.media import sign_media

    if not (location or "").strip():
        return ToolResult(ok=False, error="No address given.")
    if not gmaps.configured():
        return ToolResult(
            ok=False,
            error="No Google Maps API key configured (GOOGLE_MAPS_API_KEY).",
        )
    try:
        # Resolved first so the reply can name the place, and so a bad address
        # fails here with something sayable rather than as a broken image.
        place = gmaps.geocode(location)
    except gmaps.MapsError as e:
        return ToolResult(ok=False, error=str(e))

    return ToolResult(ok=True, data={
        "address": place["address"],
        "image_url": sign_media("street_view", place["address"]),
        "note": "Image is displayed to the user; describe it only if asked.",
    })
