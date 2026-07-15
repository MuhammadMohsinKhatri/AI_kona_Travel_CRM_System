"""Equipment name → Square device ID mapping.

Port of the n8n "Kona mapping equipments with device ids" node. Resolves the
device the driver actually used (driver-reported wins over assigned when Square
was used) and returns the Square device id plus an audit trail.
"""
from __future__ import annotations

from typing import Any

MAPPED_DEVICE_IDS: dict[str, str] = {
    "KEV1 (SM)": "522CS134A9001683",
    "KEV1": "522CS134A9001683",
    "KEV6 (SK)": "415CS149B7001332",
    "KEV6": "415CS149B7001332",
    "KEV2 (SM)": "534CS134A9000929",
    "KEV2": "534CS134A9000929",
    "KEV7": "439CS134A8000660",
    "KIOSK1": "350CS149B6000724",
    "KIOSK2 (SK)": "534CS149C3008084",
    "KIOSK2": "534CS149C3008084",
    "MINI": "420CS149B7000809",
}


def map_equipment(classification: dict[str, Any]) -> dict[str, Any]:
    square_used = str(classification.get("SQUARE_USED") or "").strip().upper() == "TRUE"

    assigned = str(classification.get("ASSIGNED_EQUIPMENT") or "").strip().upper()
    driver = str(classification.get("DRIVER_REPORTED_EQUIPMENT") or "").strip().upper()

    if square_used:
        raw_equipment = (driver or assigned or "").strip().upper()
    else:
        raw_equipment = assigned

    full_name = raw_equipment
    short_name = raw_equipment.split(" ")[0].strip().upper() if raw_equipment else ""

    device_id = MAPPED_DEVICE_IDS.get(full_name) or MAPPED_DEVICE_IDS.get(short_name)

    if square_used:
        equipment_source = "Driver Reported" if driver else "Assigned (Fallback)"
    else:
        equipment_source = "Assigned"

    equipment_mismatch = driver != "" and driver != assigned

    return {
        "device_id": device_id,
        "event_id": classification.get("EVENT_ID", ""),
        "equipment_name": raw_equipment,
        "matched_via": (
            "Full Name"
            if MAPPED_DEVICE_IDS.get(full_name)
            else "Short Name"
            if MAPPED_DEVICE_IDS.get(short_name)
            else "None"
        ),
        "status": "Match Found" if device_id else "No Device ID Mapped",
        "equipment_source": equipment_source,
        "equipment_mismatch": equipment_mismatch,
        "square_used": square_used,
        "assigned_equipment": assigned,
        "driver_reported_equipment": driver,
    }
