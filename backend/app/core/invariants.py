"""Pre-invoice consistency gate.

The classifier is an LLM, so it is wrong sometimes and no amount of prompt work
makes that never happen. What CAN be made reliable is that a wrong reading never
becomes a client's invoice. These checks are the gate: they run after the money
is calculated and BEFORE anything is drafted, and any violation holds the event
for review instead of invoicing it.

Every rule here exists because a real event was billed wrong:

  * A stated "$99 plus the cost of what we serve" was dropped entirely and the
    event billed a flat $150 (CHECK_UNUSED_AMOUNT).
  * "(no extra taxes or fees)" was read as tax-applies and 10% was added to an
    all-in quote (CHECK_ALL_IN_LANGUAGE).
  * A booking form saying "EVENT TYPE Invoice" was classified hybrid
    (CHECK_FORM_EVENT_TYPE).
  * "they will have to pay for the second one" — a guest charge — was filed as a
    host-billed per-serving rate (CHECK_OVERAGE_PAYER).

The important property is that these are CODE, not prompt instructions. A prompt
rule is a request the model may decline; this is a gate it cannot pass. That is
the whole point — the prompt tells the model what to do, and this catches it when
it doesn't.

Deliberately few and high-confidence. A gate that fires on everything gets
switched off, so a check earns its place only if a human really should look.
"""
from __future__ import annotations

import re
from typing import Any

from app.core.billing import BILLING_MODELS

# Two cents: money is rounded to 2dp upstream, and derived products
# (rate x units) can land a hair off.
_TOL = 0.02

_TAG_RE = re.compile(r"<[^>]+>")
_AMOUNT_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)")

# Amounts stated in the notes are compared against these. A figure is
# "accounted for" if it IS one of them, or if multiplying it by a count
# produces one (see _is_accounted_for).
_MONEY_FIELDS = (
    "BASE_AMOUNT", "RATE_PER_SERVING", "LOCATION_FEE", "DEPOSIT_AMOUNT",
    "ADDON_AMOUNT", "HOURLY_RATE", "MINIMUM_AMOUNT_PER_HOUR",
    "MINIMUM_FLAT_AMOUNT", "HOST_SUBSIDY_PER_SERVING", "GUEST_RATE_PER_SERVING",
    "CHECK_INVOICE_AMOUNT", "CASH_COLLECTED_AMOUNT", "DISCOUNT_AMOUNT",
)
_CALC_FIELDS = (
    "SUBTOTAL", "FINAL_INVOICE_AMOUNT", "CALCULATED_INVOICE_AMOUNT",
    "UNIT_REVENUE", "HOURLY_REVENUE", "OVERAGE_REVENUE", "SALES_AMOUNT",
    "MINIMUM_REQUIRED", "HOST_AMOUNT", "GUEST_AMOUNT", "BALANCE_DUE",
    "SALES_TAX", "CC_FEE", "TOTAL_TAX", "GIVEBACK_AMOUNT",
)
# Counts a stated per-unit price may legitimately be multiplied by. "$2 per cup,
# so 600 would come to $1,200" states a $2 that never appears as a field — it is
# accounted for only as 2 x 600.
_COUNT_FIELDS = (
    "UNITS_SERVED_TOTAL", "UNITS_INCLUDED_IN_BASE", "ATTENDEE_COUNT",
    "TOTAL_EVENT_HOURS",
)

# "no extra taxes or fees" and friends. Kept tight — a loose pattern here would
# hold events whose notes merely mention the word "tax".
_ALL_IN_PATTERNS = (
    r"no extra tax", r"no additional tax", r"no extra fee", r"no additional fee",
    r"tax included", r"taxes included", r"including tax", r"all[- ]in",
    r"out the door", r"nothing added on top", r"no taxes or fees",
)

# Language that says a GUEST pays for servings past the included allowance.
_GUEST_PAYS_PATTERNS = (
    r"they (?:will )?have to pay", r"they (?:will )?pay for",
    r"guests? (?:will )?pay", r"attendees? (?:will )?pay",
    r"pay for (?:the |their )?(?:second|2nd|extra|additional)",
    r"each guest pays", r"guests? buy",
)

_HOST_BILLED_MODELS = (
    "PACKAGE_PER_SERVING", "PACKAGE_BASE_FEE_PLUS_SERVINGS",
    "PACKAGE_FIXED", "PACKAGE_HOURLY",
)

_MG_MODELS = (
    "MIN_GUARANTEE_FLAT", "MIN_GUARANTEE_HOURLY",
    "HYBRID_SELLING_PLUS_MIN_GUARANTEE",   # retired, but stored rows still carry it
)

# Models whose money comes from what the truck actually sold. For these, a
# reconciliation of zero is a missing figure rather than a valid one.
_SALES_DRIVEN_MODELS = (
    "SELLING_OPEN", "SELLING_WITH_GIVEBACK",
) + _MG_MODELS

# The notes saying the HOST owes a bill that will be sent to them.
_HOST_INVOICED_PATTERNS = (
    r"send (?:them )?(?:an? )?invoice", r"will be paying", r"will be billed",
    r"ask to invoice", r"invoice (?:them|after|the (?:school|client|church))",
    r"bill (?:the|them)", r"host pays", r"po\b", r"purchase order",
)


def _num(v: Any) -> float:
    try:
        n = float(str(v).replace(",", "").lstrip("$"))
        return 0.0 if n != n else n
    except (TypeError, ValueError):
        return 0.0


def _notes_text(cleaned: dict[str, Any]) -> str:
    """All three note fields as one plain-text blob. Event notes arrive as HTML,
    so tags are stripped — otherwise a price inside markup is invisible here."""
    parts = [
        str(cleaned.get("EVENT_NOTES_HTML") or ""),
        str(cleaned.get("ADMIN_NOTES") or ""),
        str(cleaned.get("DRIVER_NOTES") or ""),
        str(cleaned.get("LOCATION_NOTES") or ""),
    ]
    return _TAG_RE.sub(" ", " ".join(parts))


def _stated_amounts(text: str) -> list[float]:
    seen: list[float] = []
    for raw in _AMOUNT_RE.findall(text):
        amount = _num(raw)
        if amount > 0 and not any(abs(amount - s) <= _TOL for s in seen):
            seen.append(amount)
    return seen


def _is_accounted_for(amount: float, classification: dict, calc: dict) -> bool:
    """Does this stated dollar figure do any work in the billing?

    Direct match first, then as a per-unit price times a count. The second case
    is what keeps "our cost is $2 per cup" from being flagged on an event whose
    BASE_AMOUNT is the 600-cup total.
    """
    for field in _MONEY_FIELDS:
        if abs(amount - _num(classification.get(field))) <= _TOL:
            return True
    for field in _CALC_FIELDS:
        if abs(amount - _num(calc.get(field))) <= _TOL:
            return True

    for count_field in _COUNT_FIELDS:
        count = _num(classification.get(count_field))
        if count <= 0:
            continue
        product = amount * count
        for field in _MONEY_FIELDS:
            if abs(product - _num(classification.get(field))) <= _TOL:
                return True
        for field in _CALC_FIELDS:
            if abs(product - _num(calc.get(field))) <= _TOL:
                return True
    return False


def _form_field(text: str, label: str) -> str:
    """Read a value out of the booking form's flat 'LABEL value LABEL value'
    layout. The value runs until the next all-caps label or a double space,
    because the form has no delimiters of its own."""
    m = re.search(
        rf"{label}\s+(.+?)(?=\s{{2,}}|\s+[A-Z][A-Z'’/&]+(?:\s+[A-Z][A-Z'’/&]+)*\s|$)",
        text,
        re.I,
    )
    return (m.group(1) if m else "").strip()


def check_invariants(
    cleaned: dict[str, Any],
    classification: dict[str, Any],
    calc: dict[str, Any],
    square: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Reasons this event must NOT be auto-invoiced. Empty list = safe to draft.

    Returns the same {severity, issue, action} shape the alert rules use, so
    violations ride the existing alert/notify/needs_review machinery instead of
    inventing a parallel one.
    """
    text = _notes_text(cleaned)
    low = text.lower()
    out: list[dict[str, str]] = []

    def add(issue: str, action: str, severity: str = "HIGH") -> None:
        out.append({"severity": severity, "issue": issue, "action": action})

    billing_model = str(classification.get("BILLING_MODEL") or "").upper().strip()
    event_type = str(classification.get("EVENT_TYPE") or "").strip().lower()

    # ── a billing model nobody could have chosen by hand ─────────────────────
    # The 9 in billing.BILLING_MODELS are the whole vocabulary — they mirror the
    # New Event page's "Predefined models" dropdown. A retired or invented model
    # means the event was priced by a rule the business never approved, so it
    # must not reach an invoice.
    if billing_model and billing_model not in BILLING_MODELS:
        add(
            f"Billing model '{billing_model}' is not one of the {len(BILLING_MODELS)} "
            "approved models",
            "Re-classify onto an approved model. The approved list is the "
            "Predefined models dropdown on the New Event page; anything else is "
            "either retired or invented.",
            severity="CRITICAL",
        )

    # ── the booking form disagrees with the classifier ───────────────────────
    # The form field is the office's own classification. It is not infallible,
    # but when the two disagree a person should decide which is right — not a
    # draft invoice.
    declared = _form_field(text, r"EVENT\s*TYPE").lower()
    declared_type = next(
        (t for t in ("package", "invoice", "selling", "hybrid",
                     "minimum guarantee")
         if declared.startswith(t)),
        "",
    )
    if declared_type and event_type and declared_type != event_type:
        # CRITICAL because the declared type is the office's own classification and
        # overriding it has been wrong every time so far — a terminal mention gave
        # Hybrid, the word "minimum" gave Minimum Guarantee, on notes that plainly
        # said the school would be invoiced. The type also decides whether an
        # invoice is drafted at all, so a wrong one can mean never billing anyone.
        add(
            f"Booking form says EVENT TYPE '{declared_type}' but the event was "
            f"classified '{event_type}'",
            "The form is the office's own classification — treat it as correct "
            "unless the notes explicitly contradict it. Confirm which is right "
            "before invoicing.",
            severity="CRITICAL",
        )

    declared_taxable = _form_field(text, "TAXABLE").lower()
    taxable = str(classification.get("TAXABLE") or "").upper() == "YES"
    if declared_taxable.startswith("yes") and not taxable:
        add("Booking form says TAXABLE Yes but the event was classified not taxable",
            "Confirm the tax treatment before invoicing.")
    elif declared_taxable.startswith("no") and taxable:
        add("Booking form says TAXABLE No but the event was classified taxable",
            "Confirm the tax treatment before invoicing.")

    # ── an all-in quote that had tax or a fee added on top ───────────────────
    if not str(calc.get("PRICE_IS_ALL_IN") or "").upper() in ("TRUE", "YES", "1"):
        matched = next((p for p in _ALL_IN_PATTERNS if re.search(p, low)), None)
        if matched and (_num(calc.get("SALES_TAX")) > 0 or _num(calc.get("CC_FEE")) > 0):
            add(
                "Notes rule out extra tax/fees, but tax and/or a processing fee "
                "were added on top",
                "If the quote is all-in, PRICE_IS_ALL_IN should be TRUE and the "
                "client owes the quoted figure exactly.",
            )

    # ── a guest charge filed as a host charge ────────────────────────────────
    # Billing the host for servings the guests already paid for at the truck
    # collects the same money twice.
    if (
        billing_model in _HOST_BILLED_MODELS
        and _num(classification.get("RATE_PER_SERVING")) > 0
        and any(re.search(p, low) for p in _GUEST_PAYS_PATTERNS)
    ):
        add(
            "Notes say guests pay for extra servings, but the per-serving rate is "
            "billed to the host",
            "If guests pay at the truck, the rate belongs in "
            "GUEST_RATE_PER_SERVING and the host owes only the base.",
        )

    # ── a host purchase classified as a minimum guarantee ────────────────────
    # This one is the quietest failure in the system. MG events are DEFERRED until
    # counted cash is posted, because their invoice is the shortfall against the
    # minimum. On a host-billed event no cash is ever counted, so the deferral
    # never lifts: no invoice is drafted, nothing looks wrong, and the host is
    # simply never billed. Every other misclassification at least produces a wrong
    # number somebody can see.
    if (
        billing_model in _MG_MODELS
        and any(re.search(p, low) for p in _HOST_INVOICED_PATTERNS)
        and not any(re.search(p, low) for p in _GUEST_PAYS_PATTERNS)
    ):
        add(
            "Classified as a minimum guarantee, but the notes say the host is to be "
            "invoiced and never mention guests paying",
            "A minimum guarantee is backstopped by the truck's GUEST sales. If the "
            "host is buying the servings, this is an Invoice event with the minimum "
            "in MINIMUM_FLAT_AMOUNT — otherwise the invoice is deferred waiting for "
            "cash that will never be counted, and the host is never billed.",
            severity="CRITICAL",
        )

    # ── a selling or guarantee event with no sales reconciled ────────────────
    # On a selling event the truck's Square sales ARE the revenue, and on a
    # minimum guarantee they are what counts toward the host's minimum. Zero
    # therefore means one of two very different things: the truck genuinely sold
    # nothing, or Square was never filled in for this event. Nothing in the system
    # distinguished them, so a five-hour farmers market reconciling $0.00 looked
    # exactly like a completed event with no takings.
    #
    # Left unnoticed it is worse than a wrong number: a selling event records no
    # revenue at all, and an MG event bills the host the whole minimum because the
    # sales that should have offset it are missing.
    if square is not None and billing_model in _SALES_DRIVEN_MODELS:
        breakdown = square.get("breakdown") or {}
        card = _num(breakdown.get("net_card")) or _num(square.get("total_collected"))
        orders = _num(square.get("order_count"))
        cash = _num(classification.get("CASH_COLLECTED_AMOUNT")) + _num(
            classification.get("ACTUAL_CASH_PRE_TAX")
        )
        if orders == 0 and card == 0 and cash == 0:
            if not str(square.get("device_id") or "").strip():
                add(
                    "No sales reconciled and no Square device could be matched to "
                    "this event",
                    "The equipment name in the notes did not map to a Square "
                    "device, so no orders could be found. Check the driver's "
                    "reported equipment against the device list, then re-run.",
                    severity="CRITICAL",
                )
            else:
                add(
                    "No sales at all were reconciled for this event — no Square "
                    "orders and no cash",
                    "On a selling event the Square sales ARE the revenue, so this "
                    "records none. Confirm whether the truck genuinely sold "
                    "nothing, or whether Square has not been filled in yet for "
                    "this event — if it is the latter, re-run once it has.",
                    severity="CRITICAL",
                )

    # ── a stated price that does no work ────────────────────────────────────
    # The generalised form of most misclassifications: the model acknowledges a
    # figure in its note and then leaves it out of the arithmetic.
    for amount in _stated_amounts(text):
        if not _is_accounted_for(amount, classification, calc):
            add(
                f"Notes state ${amount:,.2f} but nothing in the invoice uses it",
                "Either the figure belongs in the billing and was dropped, or it "
                "is not a price at all. Check before invoicing.",
            )

    return out
