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

    def _device_order_ids(
        self, token: str, version: str, location: str,
        device_id: str, start_iso: str, end_iso: str,
    ) -> tuple[set, list]:
        """Order ids paid on ONE Square device within the window.

        Orders/search cannot filter by device, but each card Payment carries
        card_details.device_details.device_id — so list the window's payments
        and keep the orders whose payment came from the event's assigned
        device. EXTERNAL/CASH-source payments carry no device and are
        deliberately excluded: cash is reconciled from driver notes, and
        EXTERNAL entries are invoice/online captures, not on-site truck sales
        (a single event window was seen holding $2k of them).
        """
        headers = {"Authorization": f"Bearer {token}", "Square-Version": version}
        order_ids: set = set()
        payment_ids: list = []
        cursor = None
        with httpx.Client(timeout=_TIMEOUT) as c:
            while True:
                params = {"location_id": location, "begin_time": start_iso,
                          "end_time": end_iso, "limit": 100}
                if cursor:
                    params["cursor"] = cursor
                r = c.get(f"{self.base}/v2/payments", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                for p in data.get("payments", []):
                    dev = (((p.get("card_details") or {}).get("device_details")) or {}).get("device_id")
                    if dev == device_id:
                        payment_ids.append(p.get("id"))
                        if p.get("order_id"):
                            order_ids.add(p["order_id"])
                cursor = data.get("cursor")
                if not cursor:
                    break
        return order_ids, payment_ids

    def search_orders(self, brand, device_id, date_iso, start_iso=None, end_iso=None):
        key = self._key_for(brand)
        token = self.tokens[key]
        location = self.locations[key]
        cfg = self.BRAND_CONFIG[key]
        empty = {"brand": brand, "device_id": device_id, "order_count": 0,
                 "total_collected": 0.0, "payment_ids": [], "breakdown": {}}
        if not token or not location:
            return empty
        # No mapped device or no time window → attribution is impossible; an
        # honest zero beats crediting the event with the whole location's sales.
        if not device_id or not (start_iso and end_iso):
            return empty

        body = {
            "return_entries": False,
            "limit": 1000,
            "location_ids": [location],
            "query": {
                "filter": {
                    "date_time_filter": {"closed_at": {"start_at": start_iso, "end_at": end_iso}},
                    "state_filter": {"states": cfg["states"]},
                },
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

        # Keep only orders actually paid on THIS event's device.
        device_orders, device_payments = self._device_order_ids(
            token, cfg["version"], location, device_id, start_iso, end_iso
        )
        orders = [o for o in orders if o.get("id") in device_orders]

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
            "payment_ids": device_payments,
            "breakdown": {
                "gross_sales": round(gross, 2), "discounts": round(discounts, 2),
                "net_card": net_card, "card_tax": round(card_tax, 2),
                "tips_card": round(tips, 2), "cc_fee": round(net_card * 0.04, 2),
            },
        }


import re as _re

# Per-field cap for text sent to the classifier. Notes fields occasionally
# contain pasted rich-text with embedded base64 images — one real event
# ballooned to 434k tokens and blew the model's context limit. Nothing the
# classifier needs (pricing/type language) is anywhere near this long.
_CLASSIFY_MAX_FIELD_CHARS = 6000
_DATA_URI_RE = _re.compile(r"data:[\w/+.-]+;base64,[A-Za-z0-9+/=]{100,}")


def _sanitize_for_classifier(cleaned: dict[str, Any]) -> dict[str, Any]:
    """Bound the payload sent to OpenAI without touching the stored data."""
    out: dict[str, Any] = {}
    for k, v in cleaned.items():
        if isinstance(v, str):
            v = _DATA_URI_RE.sub("[embedded image removed]", v)
            if len(v) > _CLASSIFY_MAX_FIELD_CHARS:
                v = v[:_CLASSIFY_MAX_FIELD_CHARS] + " …[truncated]"
        out[k] = v
    return out


def _clean_api_key(raw: str) -> str:
    """The API key becomes the ``Authorization`` header, and httpx encodes
    header values as ASCII — a single non-ASCII byte there raises a cryptic
    ``'ascii' codec can't encode`` deep in the request, once per event.

    Strip the whitespace/quotes that copy-paste and Windows-edited ``.env``
    files sneak in (``str.strip`` also removes non-breaking spaces and BOMs),
    then fail loudly with an actionable message if anything non-ASCII remains
    (usually a ``.env`` saved as UTF-16 or a mangled paste — the whole key is
    then mojibake and must be re-entered as plain UTF-8/ASCII).
    """
    key = (raw or "").strip().strip("\"'").strip()
    try:
        key.encode("ascii")
    except UnicodeEncodeError as exc:
        raise RuntimeError(
            "OPENAI_API_KEY contains non-ASCII characters — the key is corrupted "
            "(commonly a .env saved as UTF-16/with a BOM, or a bad copy-paste). "
            "Re-set OPENAI_API_KEY in the server .env as plain UTF-8/ASCII text."
        ) from exc
    return key


class OpenAIClassifier(Classifier):
    def __init__(self) -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=_clean_api_key(settings.openai_api_key))
        self.model = settings.openai_model
        self.system_prompt = _load_prompt()

    def classify(self, cleaned: dict[str, Any]) -> dict[str, Any]:
        user = "## ANALYZE THIS COMPLETED EVENT\n\n**PROVIDED EVENT DATA:**\n```json\n" \
               + json.dumps(_sanitize_for_classifier(cleaned)) + "\n```"
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
