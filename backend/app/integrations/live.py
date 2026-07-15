"""Live integration clients — real HTTP calls to the external systems.

These are drop-in replacements for the mocks, selected via the ``*_PROVIDER``
env vars. They read credentials from settings. Endpoints and payload shapes
mirror the original n8n HTTP Request nodes.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.integrations.base import (
    Classifier,
    CRMClient,
    Notifier,
    SheetsClient,
    SquareClient,
)

_TIMEOUT = httpx.Timeout(30.0)


class KonaCRMClient(CRMClient):
    """Client for the Kona OS CRM proxy API (Konaos_crms_apis).

    Auth is an ``X-API-Key`` header (the proxy's GPT_API_KEY). ``/events`` is
    paginated (limit ≤ 100) and returns ``{count, data: [...]}``; it filters by
    ``startDateTime`` via epoch-ms ``fromDate``/``toDate`` query params.
    """

    def __init__(self) -> None:
        self.base = settings.kona_crm_base_url.rstrip("/")
        self.headers = {"Content-Type": "application/json"}
        if settings.kona_crm_token:
            self.headers["X-API-Key"] = settings.kona_crm_token

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        with httpx.Client(timeout=_TIMEOUT) as c:
            r = c.get(f"{self.base}{path}", headers=self.headers, params=params)
            r.raise_for_status()
            return r.json()

    def list_events(self, from_ms=None, to_ms=None) -> list[dict[str, Any]]:
        page_size = 100  # API max
        offset = 0
        out: list[dict[str, Any]] = []
        while True:
            params: dict[str, Any] = {"limit": page_size, "offset": offset}
            if from_ms is not None:
                params["fromDate"] = from_ms
            if to_ms is not None:
                params["toDate"] = to_ms
            data = self._get("/events", params=params)
            rows = data if isinstance(data, list) else data.get("data", [])
            out.extend(rows)
            if len(rows) < page_size:
                break
            offset += page_size
        return out

    def get_event(self, event_id: str) -> dict[str, Any]:
        return self._get(f"/events/{event_id}")

    def list_invoices(self) -> list[dict[str, Any]]:
        data = self._get("/invoices/grid/list")
        return data if isinstance(data, list) else data.get("data", [])

    def create_invoice(self, payload: dict[str, Any]) -> dict[str, Any]:
        clean = {k: v for k, v in payload.items() if not k.startswith("_")}
        with httpx.Client(timeout=_TIMEOUT) as c:
            r = c.post(f"{self.base}/invoices", headers=self.headers, json=clean)
            r.raise_for_status()
            return r.json()

    def delete_invoice(self, invoice_id: str) -> None:
        with httpx.Client(timeout=_TIMEOUT) as c:
            r = c.delete(f"{self.base}/invoices/{invoice_id}", headers=self.headers)
            r.raise_for_status()

    def update_event(self, event_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=_TIMEOUT) as c:
            r = c.put(f"{self.base}/events/{event_id}", headers=self.headers, json=payload)
            r.raise_for_status()
            return r.json()


class SquareLiveClient(SquareClient):
    def __init__(self) -> None:
        self.base = settings.square_api_base.rstrip("/")
        self.tokens = {"kona": settings.square_kona_token, "tom": settings.square_tom_token}

    def _token_for(self, brand: str) -> str:
        b = (brand or "").lower()
        return self.tokens["tom"] if "tom" in b else self.tokens["kona"]

    def search_orders(self, brand, device_id, date_iso):
        token = self._token_for(brand)
        if not token or not device_id:
            return {"brand": brand, "device_id": device_id, "order_count": 0,
                    "total_collected": 0.0, "payment_ids": []}
        body: dict[str, Any] = {
            "location_ids": [],
            "query": {"filter": {"state_filter": {"states": ["COMPLETED"]}}},
        }
        headers = {"Authorization": f"Bearer {token}",
                   "Content-Type": "application/json",
                   "Square-Version": "2024-01-18"}
        with httpx.Client(timeout=_TIMEOUT) as c:
            r = c.post(f"{self.base}/v2/orders/search", headers=headers, json=body)
            r.raise_for_status()
            data = r.json()
        orders = data.get("orders", [])
        total = sum(o.get("total_money", {}).get("amount", 0) for o in orders) / 100.0
        return {"brand": brand, "device_id": device_id, "order_count": len(orders),
                "total_collected": round(total, 2),
                "payment_ids": [o.get("id") for o in orders]}


class OpenAIClassifier(Classifier):
    def __init__(self) -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=settings.openai_api_key)
        self.model = settings.openai_model
        self.system_prompt = _load_prompt()

    def classify(self, cleaned: dict[str, Any]) -> dict[str, Any]:
        user = "## ANALYZE THIS COMPLETED EVENT\n\n**PROVIDED EVENT DATA:**\n```json\n" \
               + json.dumps(cleaned) + "\n```"
        resp = self.client.chat.completions.create(
            model=self.model,
            response_format={"type": "json_object"},
            seed=42,  # best-effort run-to-run reproducibility of classifications
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user},
            ],
        )
        content = resp.choices[0].message.content or "{}"
        data = json.loads(content)
        out = data.get("classification_output", data)
        out.setdefault("ALERT", data.get("ALERT", []))
        # Token usage for per-run cost tracking (picked up by the pipeline).
        usage = getattr(resp, "usage", None)
        out["_usage"] = {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "model": self.model,
        }
        return out


class GoogleSheetsClient(SheetsClient):
    def __init__(self) -> None:
        import gspread
        from google.oauth2.service_account import Credentials

        info = json.loads(settings.google_service_account_json)
        creds = Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        self.gc = gspread.authorize(creds)
        self.sheet_ids = {"kona": settings.kona_sheet_id, "tom": settings.tom_sheet_id}

    def append_row(self, brand: str, row: dict[str, Any]) -> None:
        b = (brand or "").lower()
        sheet_id = self.sheet_ids["tom"] if "tom" in b else self.sheet_ids["kona"]
        month_tab = row.get("_month_tab", "Sheet1")
        sh = self.gc.open_by_key(sheet_id)
        try:
            ws = sh.worksheet(month_tab)
        except Exception:
            ws = sh.sheet1
        ws.append_row(list(row.values()))


class TelegramNotifier(Notifier):
    def __init__(self) -> None:
        self.token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id

    def send(self, message: str) -> None:
        if not self.token or not self.chat_id:
            return
        with httpx.Client(timeout=_TIMEOUT) as c:
            c.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": message, "parse_mode": "Markdown"},
            )


def _load_prompt() -> str:
    from pathlib import Path

    p = Path(__file__).resolve().parent.parent / "core" / "prompts" / "classifier_v8.md"
    return p.read_text(encoding="utf-8")
