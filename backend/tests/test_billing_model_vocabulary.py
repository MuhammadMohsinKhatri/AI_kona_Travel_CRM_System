"""The billing model list is closed.

Nine models, matching the "Predefined models" dropdown on the New Event page. A
model that cannot be picked by hand must not be invented by the classifier
either — it prices an event by a rule the business never approved.
"""
import re
from pathlib import Path

from app.core import billing
from app.core.billing import (BILLING_MODELS, calculate_invoice,
                              canonical_billing_model)
from app.core.invariants import check_invariants
from app.core.pipeline import _normalize_classification

NEW_EVENT_TSX = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "NewEvent.tsx"
)


def test_the_list_matches_the_new_event_dropdown():
    """The two lists drifting is the root cause of this whole class of bug: the UI
    offered 9, the backend accepted 11, and the classifier used the extras. Read
    the dropdown and compare, so they cannot silently diverge again."""
    source = NEW_EVENT_TSX.read_text(encoding="utf-8")
    # The PREDEFINED list entries look like: { key: "INVOICE_HOURLY", label: ... }
    ui_keys = set(re.findall(r'\{\s*key:\s*"([A-Z_]+)"', source))
    assert ui_keys, "could not parse any model keys out of NewEvent.tsx"
    assert ui_keys == set(BILLING_MODELS), (
        f"UI-only: {ui_keys - set(BILLING_MODELS)}, "
        f"backend-only: {set(BILLING_MODELS) - ui_keys}"
    )


def test_there_are_exactly_nine():
    assert len(BILLING_MODELS) == 9
    assert len(set(BILLING_MODELS)) == 9


def test_retired_models_are_not_in_the_vocabulary():
    for retired in billing.LEGACY_BILLING_MODELS:
        assert retired not in BILLING_MODELS


# ── the retired selling+minimum model maps onto MG with identical math ────────

def test_selling_plus_min_guarantee_maps_to_flat_when_the_minimum_is_flat():
    event = {"BILLING_MODEL": "HYBRID_SELLING_PLUS_MIN_GUARANTEE",
             "MINIMUM_FLAT_AMOUNT": 295}
    assert canonical_billing_model(event["BILLING_MODEL"], event) == "MIN_GUARANTEE_FLAT"


def test_selling_plus_min_guarantee_maps_to_hourly_when_stated_per_hour():
    event = {"BILLING_MODEL": "HYBRID_SELLING_PLUS_MIN_GUARANTEE",
             "MINIMUM_AMOUNT_PER_HOUR": 150, "TOTAL_EVENT_HOURS": 2}
    assert canonical_billing_model(event["BILLING_MODEL"], event) == "MIN_GUARANTEE_HOURLY"


def test_the_mapping_does_not_move_a_single_invoice():
    """The safety property. Both names routed to _mg_subtotal, so renaming must
    reproduce the old figure exactly — otherwise retiring the model would silently
    reprice historical events."""
    for extra in ({"MINIMUM_FLAT_AMOUNT": 295},
                  {"MINIMUM_AMOUNT_PER_HOUR": 150, "TOTAL_EVENT_HOURS": 2}):
        legacy = calculate_invoice({
            "BILLING_MODEL": "HYBRID_SELLING_PLUS_MIN_GUARANTEE",
            "TAXABLE": "YES", **extra,
        })
        canonical = calculate_invoice({
            "BILLING_MODEL": canonical_billing_model(
                "HYBRID_SELLING_PLUS_MIN_GUARANTEE", extra),
            "TAXABLE": "YES", **extra,
        })
        assert legacy["SUBTOTAL"] == canonical["SUBTOTAL"]
        assert legacy["FINAL_INVOICE_AMOUNT"] == canonical["FINAL_INVOICE_AMOUNT"]
        assert legacy["MINIMUM_REQUIRED"] == canonical["MINIMUM_REQUIRED"]


def test_gallery_tower_becomes_a_flat_minimum_guarantee():
    """Real event, 2026-07-25: "sell to guests with the $295 minimum". Guests
    paying at the truck while the host guarantees a minimum is a minimum
    guarantee — the retired hybrid name added nothing."""
    cls = _normalize_classification({
        "EVENT_TYPE": "hybrid",
        "BILLING_MODEL": "HYBRID_SELLING_PLUS_MIN_GUARANTEE",
        "MINIMUM_FLAT_AMOUNT": 295,
        "PAYMENT_METHOD": "CREDIT_CARD",
    })
    assert cls["BILLING_MODEL"] == "MIN_GUARANTEE_FLAT"
    assert cls["EVENT_TYPE"] == "minimum guarantee"
    assert calculate_invoice(cls)["SUBTOTAL"] == 295.0


def test_normalization_leaves_an_approved_model_alone():
    cls = _normalize_classification({
        "EVENT_TYPE": "invoice", "BILLING_MODEL": "INVOICE_HOURLY",
        "HOURLY_RATE": 295, "TOTAL_EVENT_HOURS": 1,
    })
    assert cls["BILLING_MODEL"] == "INVOICE_HOURLY"
    assert cls["EVENT_TYPE"] == "invoice"


# ── the gate refuses anything outside the nine ───────────────────────────────

def _violations(model: str) -> list[dict]:
    classification = {"EVENT_TYPE": "invoice", "BILLING_MODEL": model,
                      "BASE_AMOUNT": 100, "TAXABLE": "YES"}
    return check_invariants({}, classification, calculate_invoice(classification))


def test_an_invented_model_is_held():
    found = _violations("INVOICE_PER_HEADCOUNT_WITH_BONUS")
    assert any("not one of the 9 approved models" in v["issue"] for v in found)
    assert any(v["severity"] == "CRITICAL" for v in found)


def test_the_unmapped_retired_model_is_held_rather_than_repriced():
    """HYBRID_HOST_SUBSIDY_PLUS_GUEST_PAYMENT has no equivalent, so it is
    deliberately NOT remapped — guessing a replacement would invent a number.
    It has to surface for a person instead."""
    assert canonical_billing_model("HYBRID_HOST_SUBSIDY_PLUS_GUEST_PAYMENT") == (
        "HYBRID_HOST_SUBSIDY_PLUS_GUEST_PAYMENT"
    )
    found = _violations("HYBRID_HOST_SUBSIDY_PLUS_GUEST_PAYMENT")
    assert any("not one of the 9 approved models" in v["issue"] for v in found)


def test_every_approved_model_passes_the_vocabulary_check():
    """No false positives — none of the nine may trip the gate on its own name."""
    for model in BILLING_MODELS:
        found = _violations(model)
        assert not any("approved models" in v["issue"] for v in found), model


def test_undefined_is_also_held():
    """UNDEFINED is the RIGHT answer when no model fits — the prompt asks for it
    rather than a guess. It still must not auto-invoice, so the gate holds it for
    a person exactly like an invented model would be."""
    found = _violations("UNDEFINED")
    assert any("not one of the 9 approved models" in v["issue"] for v in found)
