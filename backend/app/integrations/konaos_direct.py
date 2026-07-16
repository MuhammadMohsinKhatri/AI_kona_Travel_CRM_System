"""Direct in-process CRM client — talks straight to KonaOS via the merged
``app.konaos.client.KonaosClient`` (no HTTP hop through the proxy layer).

Selected with ``CRM_PROVIDER=konaos``. The pipeline is synchronous, so this
wrapper owns a dedicated event loop and serialises async calls through it
(one loop for the life of the process keeps httpx connection pools valid).
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from app.integrations.base import CRMClient


class KonaOSDirectCRMClient(CRMClient):
    def __init__(self) -> None:
        from app.konaos.client import KonaosClient

        self._loop = asyncio.new_event_loop()
        self._lock = threading.Lock()
        self._kc = KonaosClient()

    def _run(self, coro):
        with self._lock:
            return self._loop.run_until_complete(coro)

    # ── CRMClient interface ──────────────────────────────────────────────

    def list_events(self, from_ms=None, to_ms=None) -> list[dict[str, Any]]:
        # Default window: last 30 days.
        if not from_ms:
            from_ms = int((time.time() - 30 * 24 * 60 * 60) * 1000)
        if not to_ms:
            to_ms = int(time.time() * 1000)

        out: list[dict[str, Any]] = []
        offset, page_size = 0, 100
        while True:
            data = self._run(self._kc.get_events_monthly(
                limit=page_size,
                offset=offset,
                sortColumn=None,
                sortType=None,
                searchText="",
                fromDate=from_ms,
                toDate=to_ms,
                applyActivatedStatus=False,
                activeEvent="",
                activated=False,
                deleted=False,
                assetIds=[],
                statusList=[],
                userIds=[],
                brandIds=self._kc.brand_ids or [],
                unAssignedAssetEvents=False,
                prePayEvent=None,
                kurbsideEvent=None,
            ))
            rows = (data or {}).get("data", []) or []
            out.extend(rows)
            if len(rows) < page_size:
                break
            offset += page_size
        return out

    def get_event(self, event_id: str) -> dict[str, Any]:
        return self._run(self._kc.get_event_details(event_id)) or {}

    def list_invoices(self) -> list[dict[str, Any]]:
        brand_ids = ",".join(str(b) for b in (self._kc.brand_ids or []) if b)
        now_ms = int(time.time() * 1000)
        data = self._run(self._kc.get_invoice_grid_list(
            brand_ids=brand_ids,
            event_date=False,
            from_date=now_ms - 30 * 24 * 60 * 60 * 1000,
            to_date=now_ms,
            search_text="",
            offset=0,
            limit=1000,
            sort_column="",
            sort_type="desc",
        ))
        if isinstance(data, list):
            return data
        return (data or {}).get("data", []) or []

    def create_invoice(self, payload: dict[str, Any]) -> dict[str, Any]:
        clean = {k: v for k, v in payload.items() if not k.startswith("_")}
        return self._run(self._kc.create_invoice(clean)) or {}

    def delete_invoice(self, invoice_id: str) -> None:
        self._run(self._kc.delete_invoice(invoice_id))

    def update_event(self, event_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        fields = {k: v for k, v in payload.items() if k != "EVENT_ID"}
        return self._run(self._kc.update_event(event_id, **fields)) or {}
