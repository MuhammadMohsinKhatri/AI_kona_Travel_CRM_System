"""Where the trucks are, and whether they need fuel.

Both read-only. Samsara can dispatch; nothing here does — see
``app/integrations/samsara.py``.

Naming a truck is optional throughout. "Where are the trucks" is the question
actually asked most mornings, and answering the whole fleet costs the same one
call as answering one of them.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.aimee.registry import ToolResult, tool
from app.integrations import samsara

# Below this a truck cannot be relied on for a full day's route without
# stopping. Brett's threshold, not a Samsara one.
LOW_FUEL_PERCENT = 25


def _unavailable(e: Exception) -> ToolResult:
    return ToolResult(ok=False, error=str(e), retryable=True)


@tool(
    name="get_truck_location",
    kind="read",
    running_label="Checking where the trucks are",
    description=(
        "Where a truck is right now — address, coordinates and whether it is "
        "moving. Omit `truck` to get the whole fleet, which is usually what is "
        "wanted. Use for 'where is the Kona truck', 'are the trucks out yet'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "truck": {
                "type": "string",
                "description": "Truck name or Samsara id. Omit for all trucks.",
            },
        },
    },
)
def get_truck_location(db: Session, truck: str = "") -> ToolResult:
    try:
        rows = samsara.locations()
    except samsara.SamsaraError as e:
        return _unavailable(e)

    if truck:
        found = samsara.find_vehicle(truck)
        if found is None:
            known = ", ".join(v["name"] for v in samsara.vehicles()) or "none"
            return ToolResult(
                ok=False,
                error=f"No truck matches \"{truck}\". Trucks on the account: {known}.",
            )
        rows = [r for r in rows if r["id"] == found["id"]]
        if not rows:
            return ToolResult(
                ok=False,
                error=(f"{found['name']} is on the account but has not reported a "
                       "position — it is probably powered down."),
            )
    if not rows:
        return ToolResult(ok=False, error="No trucks reported a position.")
    return ToolResult(ok=True, data={"trucks": rows})


@tool(
    name="get_truck_fuel",
    kind="read",
    running_label="Checking fuel levels",
    description=(
        "Fuel level per truck, as a percentage. Omit `truck` for the whole "
        "fleet. Use for 'does anything need fuel', 'how much gas is in the "
        "Catonsville truck'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "truck": {
                "type": "string",
                "description": "Truck name or Samsara id. Omit for all trucks.",
            },
        },
    },
)
def get_truck_fuel(db: Session, truck: str = "") -> ToolResult:
    try:
        rows = samsara.fuel_levels()
    except samsara.SamsaraError as e:
        return _unavailable(e)

    if truck:
        found = samsara.find_vehicle(truck)
        if found is None:
            return ToolResult(ok=False, error=f"No truck matches \"{truck}\".")
        rows = [r for r in rows if r["id"] == found["id"]]

    if not rows:
        return ToolResult(ok=False, error="No fuel readings available.")

    # "Unknown" and "empty" prompt opposite actions, so a truck with no fuel
    # sender is never counted as low — it is listed separately as unreported.
    low = [r for r in rows
           if isinstance(r.get("percent"), (int, float))
           and r["percent"] <= LOW_FUEL_PERCENT]
    unreported = [r["name"] for r in rows if not isinstance(r.get("percent"), (int, float))]
    return ToolResult(ok=True, data={
        "trucks": rows,
        "low_fuel": [r["name"] for r in low],
        "low_fuel_threshold_percent": LOW_FUEL_PERCENT,
        "no_reading": unreported,
    })
