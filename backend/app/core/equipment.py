"""Equipment name → Square device ID mapping.

Port of the n8n "Kona mapping equipments with device ids" node. Resolves the
device the driver actually used (driver-reported wins over assigned when Square
was used) and returns the Square device id plus an audit trail.
"""
from __future__ import annotations

import re
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

# Drivers name devices the way they say them out loud, not the way the CRM
# spells them. "Used terminal M" is the Mini Terminal, but neither the full
# string "TERMINAL M" nor its first word "TERMINAL" is a key above, so it used
# to resolve to no device at all — which silently means "no Square sales", and
# on a minimum-guarantee event that bills the host the FULL minimum instead of
# just the shortfall.
#
# KEV is matched first and returns early: "KEV6 terminal" must stay KEV6 rather
# than falling through to the Mini rules on the word "terminal".
_KEV_RE = re.compile(r"\bKEV\s*(\d+)\b")

_ALIAS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:MINI|M)\s*TERMINAL\b"), "MINI"),   # "M terminal", "mini terminal"
    (re.compile(r"\bTERMINAL\s*(?:MINI|M)\b"), "MINI"),   # "terminal M", "terminal mini"
    (re.compile(r"\bSQUARE\s*MINI\b"), "MINI"),
    (re.compile(r"\bMINI\b"), "MINI"),
    (re.compile(r"\bKIOSK\s*1\b"), "KIOSK1"),             # "kiosk 1"
    (re.compile(r"\bKIOSK\s*2\b"), "KIOSK2"),
]


def alias_key(raw: str) -> str:
    """Canonical device key for a loosely-written equipment name, or "" if none.

    Only consulted after an exact full-name/short-name lookup fails, so it can
    never change how an already-recognised name resolves.
    """
    if not raw:
        return ""
    text = re.sub(r"\s+", " ", raw.upper()).strip()
    kev = _KEV_RE.search(text)
    if kev:
        return f"KEV{kev.group(1)}"
    for pattern, key in _ALIAS_PATTERNS:
        if pattern.search(text):
            return key
    return ""


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

    aliased = alias_key(raw_equipment)
    device_id = (
        MAPPED_DEVICE_IDS.get(full_name)
        or MAPPED_DEVICE_IDS.get(short_name)
        or MAPPED_DEVICE_IDS.get(aliased)
    )

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
            else f"Alias ({aliased})"
            if MAPPED_DEVICE_IDS.get(aliased)
            else "None"
        ),
        "status": "Match Found" if device_id else "No Device ID Mapped",
        "equipment_source": equipment_source,
        "equipment_mismatch": equipment_mismatch,
        "square_used": square_used,
        "assigned_equipment": assigned,
        "driver_reported_equipment": driver,
    }
