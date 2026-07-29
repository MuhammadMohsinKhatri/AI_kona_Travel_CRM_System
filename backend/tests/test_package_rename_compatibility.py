"""Backwards compatibility for the INVOICE_* -> PACKAGE_* rename.

Every event stored before the rename carries EVENT_TYPE "invoice" and a
BILLING_MODEL named INVOICE_*. build_invoice_payload returns None for an
unrecognised event type, so if those values stopped resolving the system would
silently stop drafting invoices for historical events — no error, no alert, just
nothing. These tests are the guard.
"""
from app.core import billing
from app.core.billing import (BILLING_MODELS, calculate_invoice,
                              canonical_billing_model, canonical_event_type)
from app.core.invoice_builder import build_invoice_payload
from app.core.pipeline import _normalize_classification
from app.core.rule_classifier import _LABEL_TO_TYPE, _MODEL_TO_TYPES

LEGACY_TO_CURRENT = {
    "INVOICE_PER_SERVING": "PACKAGE_PER_SERVING",
    "INVOICE_BASE_FEE_PLUS_SERVINGS": "PACKAGE_BASE_FEE_PLUS_SERVINGS",
    "INVOICE_FIXED_PACKAGE": "PACKAGE_FIXED",
    "INVOICE_HOURLY": "PACKAGE_HOURLY",
}


def test_every_legacy_model_name_maps_to_a_current_one():
    for legacy, current in LEGACY_TO_CURRENT.items():
        assert canonical_billing_model(legacy) == current
        assert current in BILLING_MODELS


def test_the_legacy_event_type_maps_to_package():
    assert canonical_event_type("invoice") == "package"
    assert canonical_event_type("Invoice") == "package"
    assert canonical_event_type("package") == "package"
    # Untouched types pass straight through.
    for other in ("selling", "hybrid", "minimum guarantee"):
        assert canonical_event_type(other) == other


def test_a_legacy_model_prices_identically_to_its_new_name():
    """A pure relabel must not move a single stored figure."""
    shared = {
        "BASE_AMOUNT": 99, "RATE_PER_SERVING": 4, "UNITS_SERVED_TOTAL": 23,
        "UNITS_INCLUDED_IN_BASE": 10, "HOURLY_RATE": 250,
        "TOTAL_EVENT_HOURS": 1, "TAXABLE": "YES", "PAYMENT_METHOD": "CHECK",
    }
    for legacy, current in LEGACY_TO_CURRENT.items():
        old = calculate_invoice({**shared, "BILLING_MODEL": legacy})
        new = calculate_invoice({**shared, "BILLING_MODEL": current})
        assert old["SUBTOTAL"] == new["SUBTOTAL"], legacy
        assert old["FINAL_INVOICE_AMOUNT"] == new["FINAL_INVOICE_AMOUNT"], legacy


def test_a_legacy_event_still_drafts_an_invoice():
    """The failure this rename could have caused: build_invoice_payload gates on
    the event type, so a stored "invoice" that no longer resolved would return
    None and the event would quietly never be billed."""
    classification = {
        "EVENT_TYPE": "invoice",                  # pre-rename value
        "BILLING_MODEL": "INVOICE_PER_SERVING",   # pre-rename name
        "RATE_PER_SERVING": 3, "UNITS_SERVED_TOTAL": 100, "TAXABLE": "YES",
    }
    calc = calculate_invoice(classification)
    assert calc["SUBTOTAL"] == 300.0

    payload = build_invoice_payload(
        {**classification, "calculations": calc}, {"DATE": "2026-07-01"}, {})
    assert payload is not None, "a legacy event stopped producing an invoice"
    assert payload["invoiceType"] == "Invoice"   # KonaOS's own vocabulary is unchanged
    assert payload["grandTotal"] == calc["FINAL_INVOICE_AMOUNT"]


def test_a_current_event_drafts_an_invoice_too():
    classification = {
        "EVENT_TYPE": "package", "BILLING_MODEL": "PACKAGE_PER_SERVING",
        "RATE_PER_SERVING": 3, "UNITS_SERVED_TOTAL": 100, "TAXABLE": "YES",
    }
    calc = calculate_invoice(classification)
    payload = build_invoice_payload(
        {**classification, "calculations": calc}, {"DATE": "2026-07-01"}, {})
    assert payload is not None
    assert payload["invoiceType"] == "Invoice"


def test_normalization_rewrites_a_legacy_pair_to_current_names():
    cls = _normalize_classification({
        "EVENT_TYPE": "invoice", "BILLING_MODEL": "INVOICE_FIXED_PACKAGE",
        "BASE_AMOUNT": 1200, "UNITS_INCLUDED_IN_BASE": 600,
    })
    assert cls["EVENT_TYPE"] == "package"
    assert cls["BILLING_MODEL"] == "PACKAGE_FIXED"


def test_the_rule_classifier_form_label_still_cross_checks():
    """The office's form says "EVENT TYPE: Invoice". _MODEL_TO_TYPES now says
    "package", and rule_classifier bails to the LLM when the two disagree — so a
    mismatch here would send every form-generated host-billed event to the model
    at full token cost, silently."""
    assert _LABEL_TO_TYPE["invoice"] == "package"
    assert _LABEL_TO_TYPE["package"] == "package"
    for model, allowed in _MODEL_TO_TYPES.items():
        if model.startswith("PACKAGE_"):
            # Package models are valid on a package event and on a hybrid, which is
            # a package that also makes sales.
            assert "package" in allowed, model
            assert "hybrid" in allowed, model
        # Every type a model allows must be reachable from some form label.
        for t_ in allowed:
            assert t_ in set(_LABEL_TO_TYPE.values()), (model, t_)


def test_no_legacy_invoice_model_names_remain_in_the_vocabulary():
    for legacy in LEGACY_TO_CURRENT:
        assert legacy not in BILLING_MODELS
    assert all(not m.startswith("INVOICE_") for m in BILLING_MODELS)
    assert all(m in BILLING_MODELS for m in billing.FLOORED_BILLING_MODELS)
