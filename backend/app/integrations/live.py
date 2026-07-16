"""Live integration clients — real HTTP calls to the external systems.

These are drop-in replacements for the mocks, selected via the ``*_PROVIDER``
env vars. They read credentials from settings. Endpoints and payload shapes
mirror the original n8n HTTP Request nodes.

The CRM has no "live" HTTP client here — ``CRM_PROVIDER=konaos`` uses the
in-process KonaOS client (``app.integrations.konaos_direct`` → ``app.konaos``).
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import settings
from app.integrations.base import (
    Classifier,
    Notifier,
    SheetsClient,
    SquareClient,
)

_TIMEOUT = httpx.Timeout(30.0)


class SquareLiveClient(SquareClient):
    """Square Orders/Search per brand. Query shape matches the original n8n
    nodes: filter by ``closed_at`` between the event's actual start/end
    (NY→UTC), per-brand location + token + API version + state filter. Computes
    the sheet's Square breakdown (gross, discounts, net card, tax, tips, 4% CC fee).

    Kona and Tom's are separate Square accounts with different API versions and
    state filters, so those are configured per brand here.
    """

    # Per-brand Square API config (from the live n8n nodes).
    BRAND_CONFIG = {
        "kona": {"version": "2026-01-22", "states": ["COMPLETED", "OPEN"]},
        "tom": {"version": "2025-10-16", "states": ["COMPLETED"]},
    }

    def __init__(self) -> None:
        self.base = settings.square_api_base.rstrip("/")
        self.tokens = {"kona": settings.square_kona_token, "tom": settings.square_tom_token}
        self.locations = {
            "kona": settings.square_kona_location,
            "tom": settings.square_tom_location,
        }

    def _key_for(self, brand: str) -> str:
        return "tom" if "tom" in (brand or "").lower() else "kona"

    def search_orders(self, brand, device_id, date_iso, start_iso=None, end_iso=None):
        key = self._key_for(brand)
        token = self.tokens[key]
        location = self.locations[key]
        cfg = self.BRAND_CONFIG[key]
        empty = {"brand": brand, "device_id": device_id, "order_count": 0,
                 "total_collected": 0.0, "payment_ids": [], "breakdown": {}}
        if not token or not location:
            return empty

        date_filter: dict[str, Any] = {}
        if start_iso and end_iso:
            date_filter = {"date_time_filter": {"closed_at": {"start_at": start_iso, "end_at": end_iso}}}
        body = {
            "return_entries": False,
            "limit": 1000,
            "location_ids": [location],
            "query": {
                "filter": {**date_filter, "state_filter": {"states": cfg["states"]}},
                "sort": {"sort_field": "UPDATED_AT", "sort_order": "DESC"},
            },
        }
        headers = {"Authorization": f"Bearer {token}",
                   "Content-Type": "application/json",
                   "Square-Version": cfg["version"]}
        with httpx.Client(timeout=_TIMEOUT) as c:
            r = c.post(f"{self.base}/v2/orders/search", headers=headers, json=body)
            r.raise_for_status()
            orders = r.json().get("orders", [])

        def money(o, field):
            return (o.get(field, {}) or {}).get("amount", 0) / 100.0

        gross = sum(money(o, "total_money") - money(o, "total_tax_money") -
                    money(o, "total_tip_money") + money(o, "total_discount_money") for o in orders)
        discounts = sum(money(o, "total_discount_money") for o in orders)
        card_tax = sum(money(o, "total_tax_money") for o in orders)
        tips = sum(money(o, "total_tip_money") for o in orders)
        net_card = round(gross - discounts, 2)
        return {
            "brand": brand, "device_id": device_id, "location": location,
            "order_count": len(orders),
            "total_collected": net_card,
            "payment_ids": [o.get("id") for o in orders],
            "breakdown": {
                "gross_sales": round(gross, 2), "discounts": round(discounts, 2),
                "net_card": net_card, "card_tax": round(card_tax, 2),
                "tips_card": round(tips, 2), "cc_fee": round(net_card * 0.04, 2),
            },
        }


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
