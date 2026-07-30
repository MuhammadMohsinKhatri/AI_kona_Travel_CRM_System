"""A fingerprint of the KonaOS fields an invoice is derived from.

Why this exists: ThriftBooks (2026-07-29) was processed before its driver typed
"Served 31 Konas". Our copy said 0 servings and the invoice came to $0.00, and
nothing anywhere said the source had moved on — the event just sat there looking
finished. Drivers and admins routinely fill notes in after the fact, so any
event processed once is a snapshot that can go stale silently.

Comparing fingerprints is how the watcher (app/tasks/watch_tasks.py) notices.
KonaOS exposes an ``updatedAt`` and it is tempting to use it instead, but the
pipeline itself PUTs financial actuals back onto non-package events — that write
bumps updatedAt, which would look like a change, trigger a re-run, and bump it
again. A fingerprint over the INPUT fields only cannot chase its own tail.

So the field list below is the contract, and the exclusions matter as much as
the inclusions:

  * EVENT_SALES / NET_EVENT_SALES / BALANCE / SALES_TAX / TIP_AMOUNT — written
    by our own sync step (pipeline.py), so including them means re-running
    forever.
  * UPDATED_AT / CREATED_AT — same problem, plus they move for edits to fields
    that can't change a bill (a phone number, a colour tag).
  * Contact and address details — a corrected phone number is not a reason to
    redraft an invoice.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

# Everything that can change what the client owes. Sorted so the hash is stable.
BILLING_SOURCE_FIELDS: tuple[str, ...] = (
    # What was agreed and what happened — the notes are the whole ballgame.
    "ADMIN_NOTES",
    "DRIVER_NOTES",
    "EVENT_NOTES_HTML",
    "LOCATION_NOTES",
    # Timing: drives the hourly models and the Square reconciliation window.
    "DATE",
    "EVENT_STARTED",
    "EVENT_ENDED",
    # Which truck served it — decides which Square device's sales are this
    # event's, and whether a Pending event counts as worked.
    "EQUIPMENT",
    "EQUIPMENT_IDS",
    "STAFF_ASSIGNED",
    "STAFF_COUNT",
    # Whether we process it at all (is_processable) and how.
    "EVENT_STATUS_MANUAL",
    "EVENT_STATUS_SYSTEM",
    "FINAL_EVENT_STATUS",
    "EVENT_TYPE",
    "PAYMENT_TERM",
    "CLIENT_INVOICE",
    # A stated delivery/destination fee is a real line on a host-billed event.
    "DELIVERY_FEE",
    # Renames matter: the invoice title and the client record follow the name.
    "EVENT_NAME",
    "BRAND",
    "EVENT_LOCATION",
)


def fingerprint(cleaned: dict[str, Any]) -> str:
    """Stable hash of the billing-relevant fields of a cleaned event.

    Values are normalized to strings with surrounding whitespace stripped, so
    KonaOS re-serializing "Served 31 Konas. Send invoice " with a different
    trailing space doesn't read as an edit.
    """
    payload = {
        field: str(cleaned.get(field) if cleaned.get(field) is not None else "").strip()
        for field in BILLING_SOURCE_FIELDS
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
