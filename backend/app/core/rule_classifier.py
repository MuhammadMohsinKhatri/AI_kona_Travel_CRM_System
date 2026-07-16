"""Deterministic classifier for structured (form-generated) event notes.

The New Event form writes ADMIN / EVENT / DRIVER notes from fixed templates
(see frontend NewEvent.tsx buildAdminNotes / buildEventNotes / buildDriverNotes).
When an event's notes consist ONLY of those known sentences, this module
extracts the classification in code — exact numbers, zero AI cost, instant.

Safety rule: every sentence must match a known template. One unrecognized
sentence (a human typed free-text pricing prose) → return None and let the
LLM classifier handle the whole event. We never guess.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

_AMT = r"\$?([\d,]+(?:\.\d+)?)"


def _num(s: str) -> float:
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


# ── ADMIN NOTES sentence templates (anchored; one regex per known sentence) ──
# Each entry: (regex, handler(match, out)). Handlers mutate the classification.
def _set(key: str, group: int = 1, cast=_num):
    def h(m: re.Match, out: dict) -> None:
        out[key] = cast(m.group(group))
    return h


def _mark(key: str, value: Any):
    def h(m: re.Match, out: dict) -> None:
        out[key] = value
    return h


def _model(name: str, *setters):
    def h(m: re.Match, out: dict) -> None:
        out["BILLING_MODEL"] = name
        for i, key in enumerate(setters, start=1):
            if key:
                out[key] = _num(m.group(i))
    return h


_ADMIN_SENTENCES: list[tuple[re.Pattern, Any]] = [
    (re.compile(rf"^{_AMT} per serving\.?$", re.I),
     _model("INVOICE_PER_SERVING", "RATE_PER_SERVING")),
    (re.compile(rf"^Setup fee {_AMT} plus {_AMT} per serving\.?$", re.I),
     _model("INVOICE_BASE_FEE_PLUS_SERVINGS", "BASE_AMOUNT", "RATE_PER_SERVING")),
    (re.compile(rf"^{_AMT} covers up to ([\d,]+) servings, each additional {_AMT} a piece\.?$", re.I),
     _model("INVOICE_FIXED_PACKAGE", "BASE_AMOUNT", "UNITS_INCLUDED_IN_BASE", "RATE_PER_SERVING")),
    (re.compile(rf"^{_AMT} per hour\.?$", re.I),
     _model("INVOICE_HOURLY", "HOURLY_RATE")),
    (re.compile(r"^Send invoice\.?$", re.I), lambda m, o: None),
    (re.compile(r"^Open selling event\.?$", re.I), _model("SELLING_OPEN")),
    (re.compile(r"^Guests pay individually\.?$", re.I), lambda m, o: None),
    (re.compile(r"^Selling event\.?$", re.I), _model("SELLING_OPEN")),
    (re.compile(rf"^Giveback percentage: {_AMT}%\.?$", re.I),
     _set("GIVEBACK_PERCENTAGE")),
    (re.compile(rf"^Minimum guarantee {_AMT} flat\.?$", re.I),
     _model("MIN_GUARANTEE_FLAT", "MINIMUM_FLAT_AMOUNT")),
    (re.compile(rf"^Minimum guarantee {_AMT} per hour\.?$", re.I),
     _model("MIN_GUARANTEE_HOURLY", "MINIMUM_AMOUNT_PER_HOUR")),
    (re.compile(r"^Host covers shortfall\.?$", re.I), lambda m, o: None),
    (re.compile(rf"^Host pays {_AMT} base covering ([\d,]+) servings\.?$", re.I),
     _model("HYBRID_HOST_BASE_PLUS_GUEST_EXTRA", "BASE_AMOUNT", "UNITS_INCLUDED_IN_BASE")),
    (re.compile(rf"^Additional servings {_AMT} billed to host\.?$", re.I),
     _set("RATE_PER_SERVING")),
    (re.compile(rf"^Guests pay {_AMT} per serving for extras\.?$", re.I),
     _set("GUEST_RATE_PER_SERVING")),
    (re.compile(rf"^Plus {_AMT} for (.+?)\.?$", re.I),
     lambda m, o: (o.__setitem__("ADDON_AMOUNT", _num(m.group(1))),
                   o.__setitem__("ADDON_LABEL", m.group(2).strip()))),
    (re.compile(rf"^{_AMT} location fee\.?$", re.I), _set("LOCATION_FEE")),
    (re.compile(rf"^Deposit {_AMT} required\.?$", re.I), _set("DEPOSIT_AMOUNT")),
    (re.compile(rf"^Discount {_AMT} applied\.?$", re.I), _set("DISCOUNT_AMOUNT")),
    (re.compile(r"^Client is tax exempt\.?$", re.I), _mark("TAXABLE", "NO")),
    (re.compile(r"^Plus tax\.?$", re.I), _mark("TAXABLE", "YES")),
    (re.compile(r"^Quoted total is all-in \(tax and fee included\)\.?$", re.I),
     _mark("PRICE_IS_ALL_IN", "TRUE")),
    (re.compile(r"^Card only, no on-site cash\.?$", re.I), lambda m, o: None),
]

_MODEL_TO_TYPE = {
    "INVOICE_PER_SERVING": "invoice",
    "INVOICE_BASE_FEE_PLUS_SERVINGS": "invoice",
    "INVOICE_FIXED_PACKAGE": "invoice",
    "INVOICE_HOURLY": "invoice",
    "SELLING_OPEN": "selling",
    "SELLING_WITH_GIVEBACK": "selling",
    "MIN_GUARANTEE_FLAT": "minimum guarantee",
    "MIN_GUARANTEE_HOURLY": "minimum guarantee",
    "HYBRID_HOST_BASE_PLUS_GUEST_EXTRA": "hybrid",
}

# Form label (EVENT TYPE: ...) → normalized event type.
_LABEL_TO_TYPE = {
    "invoice": "invoice",
    "selling": "selling",
    "min guarantee": "minimum guarantee",
    "minimum guarantee": "minimum guarantee",
    "hybrid": "hybrid",
}


def _strip_html(html: str) -> list[str]:
    text = re.sub(r"<br\s*/?>", "\n", html or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    return [ln.strip() for ln in text.split("\n") if ln.strip()]


def _split_sentences(text: str) -> list[str]:
    # Templates are simple period-terminated sentences; amounts never contain
    # ". " sequences, so splitting on period+whitespace is safe.
    parts = re.split(r"(?<=\.)\s+", (text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def _hours(cleaned: dict[str, Any]) -> float:
    try:
        s = datetime.fromisoformat(cleaned.get("EVENT_STARTED"))
        e = datetime.fromisoformat(cleaned.get("EVENT_ENDED"))
        return max(0.0, round((e - s).total_seconds() / 3600.0, 2))
    except (TypeError, ValueError):
        return 0.0


def try_rule_classify(cleaned: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Return a full classification dict if the notes are fully structured,
    else None (caller falls back to the LLM)."""
    admin = (cleaned.get("ADMIN_NOTES") or "").strip()
    event_lines = _strip_html(cleaned.get("EVENT_NOTES_HTML") or "")
    driver = (cleaned.get("DRIVER_NOTES") or "").strip()

    if not admin:
        return None

    out: dict[str, Any] = {}

    # ── ADMIN NOTES: every sentence must match a known template ──────────────
    matched_any_model = False
    for sentence in _split_sentences(admin):
        for rx, handler in _ADMIN_SENTENCES:
            m = rx.match(sentence)
            if m:
                handler(m, out)
                if out.get("BILLING_MODEL"):
                    matched_any_model = True
                break
        else:
            return None  # free-text sentence → AI's job
    if not matched_any_model:
        return None

    # Giveback on a selling event upgrades the model (form writes it as a
    # separate sentence).
    if out.get("BILLING_MODEL") == "SELLING_OPEN" and _num(out.get("GIVEBACK_PERCENTAGE", 0)) > 0:
        out["BILLING_MODEL"] = "SELLING_WITH_GIVEBACK"

    billing_model = out["BILLING_MODEL"]
    event_type = _MODEL_TO_TYPE[billing_model]

    # ── EVENT NOTES: require the form's EVENT TYPE label and cross-check ─────
    label_type = None
    attendees = 0
    for ln in event_lines:
        m = re.match(r"^EVENT TYPE:\s*(.+)$", ln, re.I)
        if m:
            label_type = _LABEL_TO_TYPE.get(m.group(1).strip().lower())
            continue
        m = re.match(r"^ATTENDEES:\s*([\d,]+)", ln, re.I)
        if m:
            attendees = int(_num(m.group(1)))
        # Other labeled lines (SERVE & KEEP COUNT / PARKING / ADD'L INSTRUCTION)
        # are informational — no pricing meaning, safe to ignore.
    if label_type is None or label_type != event_type:
        return None  # missing/contradicting label → let the LLM arbitrate

    # ── DRIVER NOTES: labeled actuals only; anything else → AI ───────────────
    units_served = 0.0
    paid = False
    payment_method = ""
    cash_amount = 0.0
    square_device = ""
    for ln in [l.strip() for l in driver.split("\n") if l.strip()]:
        m = re.match(rf"^ACTUAL SERVING COUNT:\s*([\d,]+(?:\.\d+)?)$", ln, re.I)
        if m:
            units_served = _num(m.group(1)); continue
        m = re.match(r"^PAID:\s*(Check|Credit Card|Cash|yes)$", ln, re.I)
        if m:
            paid = True
            v = m.group(1).lower()
            payment_method = {"check": "CHECK", "credit card": "CREDIT_CARD",
                              "cash": "CASH"}.get(v, "")
            continue
        m = re.match(rf"^CASH COLLECTED:\s*{_AMT}$", ln, re.I)
        if m:
            cash_amount = _num(m.group(1)); continue
        m = re.match(r"^SQUARE DEVICE:\s*(.+)$", ln, re.I)
        if m:
            square_device = m.group(1).strip(); continue
        return None  # ACTUAL TIMES / free text → time math is the LLM's job

    taxable = out.get("TAXABLE", "YES")
    if not payment_method:
        payment_method = "CHECK" if event_type in ("invoice", "minimum guarantee", "hybrid") else "CREDIT_CARD"
    if cash_amount > 0:
        payment_method = "CASH"

    hours = _hours(cleaned)

    return {
        "EVENT_ID": cleaned.get("EVENT_ID", ""),
        "EVENT_NAME": cleaned.get("EVENT_NAME", ""),
        "EVENT_DATE": cleaned.get("DATE", ""),
        "EVENT_TYPE": event_type,
        "BILLING_MODEL": billing_model,
        "TAXABLE": taxable,
        "TAX_RATE_sales": 0.06 if taxable == "YES" else 0,
        "PROCESSING_FEE_RATE": 0.04,
        "MG_SHORTFALL": 0,
        "PAYMENT_METHOD": payment_method,
        "PAID_STATUS": paid,
        "PRIMARY_WORKER": (cleaned.get("STAFF_ASSIGNED") or "").split(",")[0].strip(),
        "HOURS": hours,
        "TOTAL_EVENT_HOURS": hours,
        "HOURLY_RATE": out.get("HOURLY_RATE", 0),
        "ACTUAL_EVENT_START_TIME": "",
        "ACTUAL_EVENT_END_TIME": "",
        "ATTENDEE_COUNT": attendees,
        "UNITS_SERVED_TOTAL": units_served,
        "UNITS_INCLUDED_IN_BASE": out.get("UNITS_INCLUDED_IN_BASE", 0),
        "BASE_AMOUNT": out.get("BASE_AMOUNT", 0),
        "BASE_IS_FIXED_COMMITMENT": "TRUE",
        "RATE_PER_SERVING": out.get("RATE_PER_SERVING", 0),
        "LOCATION_FEE": out.get("LOCATION_FEE", 0),
        "MINIMUM_AMOUNT_PER_HOUR": out.get("MINIMUM_AMOUNT_PER_HOUR", 0),
        "MINIMUM_FLAT_AMOUNT": out.get("MINIMUM_FLAT_AMOUNT", 0),
        "GIVEBACK_PERCENTAGE": _num(out.get("GIVEBACK_PERCENTAGE", 0)) / 100.0,
        "HOST_SUBSIDY_PER_SERVING": 0,
        "GUEST_RATE_PER_SERVING": out.get("GUEST_RATE_PER_SERVING", 0),
        "DEPOSIT_AMOUNT": out.get("DEPOSIT_AMOUNT", 0),
        "DISCOUNT_PERCENT": 0,
        "DISCOUNT_AMOUNT": out.get("DISCOUNT_AMOUNT", 0),
        "SQUARE_USED": "TRUE" if square_device else "FALSE",
        "SQUARE_DEVICE_CONFIDENCE": "HIGH" if square_device else "LOW",
        "ASSIGNED_EQUIPMENT": cleaned.get("EQUIPMENT", "") or "",
        "DRIVER_REPORTED_EQUIPMENT": square_device,
        "ACTUAL_TIME_FOUND": "FALSE",
        "SERVING_COUNT_SOURCE": "driver_notes" if units_served else "",
        "CASH_COLLECTED_DETECTED": "TRUE" if cash_amount > 0 else "FALSE",
        "CASH_COLLECTED_AMOUNT": cash_amount,
        "CHECK_INVOICE_AMOUNT": 0,
        "ADDON_AMOUNT": out.get("ADDON_AMOUNT", 0),
        "ADDON_LABEL": out.get("ADDON_LABEL", ""),
        "PRICE_IS_ALL_IN": out.get("PRICE_IS_ALL_IN", "FALSE"),
        "NOTE": "Deterministic parse of structured New Event form notes — no AI used.",
        "ALERT": [],
        "_usage": {"prompt_tokens": 0, "completion_tokens": 0, "model": "rule-based"},
        "_rule_based": True,
    }
