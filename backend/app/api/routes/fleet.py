"""One combined snapshot for the Fleet & Staff page: where the trucks are,
how much fuel they have, and who is on the clock right now.

Chat can answer these one at a time (app/aimee/tools/fleet.py, labor.py); this
endpoint exists because "how's everything looking" is a page you land on, not
a question you type. It calls the same integration modules the chat tools do —
no separate logic to drift from them.

Each of the three sources fails independently. A dead Samsara token must not
blank out the Square section, and vice versa — the office asking "is anyone
clocked in" shouldn't get nothing back because a truck's fuel sensor is
offline. Every section carries its own ``ok``/``error`` rather than the
endpoint raising.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.base import get_db
from app.integrations import gmaps, samsara, square_labor
from app.models import User

router = APIRouter(prefix="/api/fleet", tags=["fleet"])


def _trucks() -> dict[str, Any]:
    if not samsara.configured():
        return {"ok": False, "error": "Samsara isn't configured yet.", "trucks": []}
    try:
        locs = {r["id"]: r for r in samsara.locations()}
        fuel = {r["id"]: r for r in samsara.fuel_levels()}
    except samsara.SamsaraError as e:
        return {"ok": False, "error": str(e), "trucks": []}

    trucks = []
    for v in samsara.vehicles():
        loc = locs.get(v["id"], {})
        fl = fuel.get(v["id"], {})
        trucks.append({
            "id": v["id"],
            "name": v["name"],
            "address": loc.get("address") or "",
            "latitude": loc.get("latitude"),
            "longitude": loc.get("longitude"),
            "position_at": loc.get("at") or "",
            "fuel_percent": fl.get("percent"),
            "low_fuel": (isinstance(fl.get("percent"), (int, float))
                        and fl["percent"] <= samsara.LOW_FUEL_PERCENT),
        })
    return {"ok": True, "error": "", "trucks": trucks}


def _staff() -> dict[str, Any]:
    if not square_labor.configured():
        return {"ok": False, "error": "Square isn't configured yet.", "on_the_clock": []}
    try:
        rows = square_labor.on_the_clock()
    except square_labor.SquareLaborError as e:
        return {"ok": False, "error": str(e), "on_the_clock": []}
    return {"ok": True, "error": "", "on_the_clock": rows}


@router.get("/status")
def fleet_status(_: User = Depends(get_current_user)) -> dict[str, Any]:
    return {
        "trucks": _trucks(),
        "staff": _staff(),
        "maps_configured": gmaps.configured(),
    }


class EtaRequest(BaseModel):
    truck: str
    destination: str


@router.post("/eta")
def fleet_eta(
    body: EtaRequest, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> dict[str, Any]:
    """The Fleet page's ETA widget — a REST wrapper over the same
    get_truck_eta Aimee already calls, so typing a destination on this page
    and asking Aimee the same question can never disagree.

    Chat is a better fit for the OTHER travel tools (a route with several
    stops, an arbitrary point-to-point time, a Street View photo) — each needs
    fresh input every time regardless of surface, so a page for them would be
    the same form chat already is, with none of the conversation.
    """
    from app.aimee.tools.maps import get_truck_eta

    result = get_truck_eta(db=db, truck=body.truck, destination=body.destination)
    if not result.ok:
        return {"ok": False, "error": result.error}
    return {"ok": True, **result.data}
