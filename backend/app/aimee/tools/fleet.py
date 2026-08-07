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
from app.integrations.samsara import LOW_FUEL_PERCENT


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
        "Catonsville truck', 'fuel report'. Each truck comes back with a "
        "`marker` — show it beside the truck in your answer, in a Status "
        "column if you use a table. Trucks arrive worst-first; keep that order."
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

    # The status travels WITH each reading. Handed bare percentages, the model
    # printed a plain table and 15% sat there looking like any other number —
    # it had no way to know which one mattered. Same helper the nightly
    # Telegram report uses, so chat and phone cannot disagree about "low".
    for r in rows:
        r["status"], r["marker"] = samsara.fuel_status(r.get("percent"))

    # Worst first, unknowns last: the truck that needs doing something about
    # should not be fourth in a list.
    rows.sort(key=lambda r: (
        (1, 0.0) if not isinstance(r.get("percent"), (int, float))
        else (0, float(r["percent"]))
    ))

    # "Unknown" and "empty" prompt opposite actions, so a truck with no fuel
    # sender is never counted as low — it is listed separately as unreported.
    low = [r for r in rows if r["status"] in ("low", "critical")]
    unreported = [r["name"] for r in rows if r["status"] == "unknown"]
    return ToolResult(ok=True, data={
        "trucks": rows,
        "low_fuel": [r["name"] for r in low],
        "low_fuel_threshold_percent": LOW_FUEL_PERCENT,
        "critical_threshold_percent": samsara.CRITICAL_FUEL_PERCENT,
        "no_reading": unreported,
        "legend": "🔴 critical · 🟠 low · 🟢 ok · ⚪ no reading",
    })
