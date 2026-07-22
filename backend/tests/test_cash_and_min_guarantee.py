"""Cash overrides and min-guarantee shortfall billing.

These cover the two behaviours that move money and that nothing else guards:

  * A min-guarantee host pays only the GAP between actual sales and the
    minimum they guaranteed — not the whole minimum. Getting this wrong
    over-bills a customer whose truck sold well.
  * Cash counted after the event must survive the next pipeline run. If a
    re-run recomputes cash from the driver's notes, the real figure is
    silently replaced by a guess (usually 0) and every derived total is wrong.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_konaice.db")
os.environ.setdefault("PIPELINE_RUN_INLINE", "true")
os.environ.setdefault("MOCK_LATENCY_S", "0")
# Force mocks regardless of .env — tests must NEVER touch production.
os.environ["CRM_PROVIDER"] = "mock"
os.environ["SQUARE_PROVIDER"] = "mock"
os.environ["OPENAI_PROVIDER"] = "mock"
os.environ["TELEGRAM_PROVIDER"] = "mock"
os.environ["PIPELINE_DRY_RUN"] = "false"

from app.core import overrides  # noqa: E402
from app.core.billing import calculate_invoice  # noqa: E402
from app.core.invoice_builder import build_invoice_payload  # noqa: E402
from app.core.pipeline import run_pipeline  # noqa: E402
from app.db.base import Base, SessionLocal, engine  # noqa: E402
from app.models import FinancialEntry, PipelineRun  # noqa: E402


def setup_module(_):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


# ── Min-guarantee: the invoice is the gap, not the minimum ──────────────────

def _mg(minimum: float, card: float, cash: float, *, known: bool = True, location_fee: float = 0.0):
    return calculate_invoice({
        "BILLING_MODEL": "MIN_GUARANTEE_FLAT",
        "MINIMUM_FLAT_AMOUNT": minimum,
        "LOCATION_FEE": location_fee,
        "TAXABLE": "NO",          # keep the arithmetic readable
        "PAYMENT_METHOD": "CHECK",
        "PAID_STATUS": "TRUE",    # confirmed check payment → no 4% card fee
        "ACTUAL_CARD_SALES": card,
        "ACTUAL_CASH_PRE_TAX": cash,
        "ACTUAL_SALES_KNOWN": known,
    })


def test_mg_bills_only_the_shortfall_not_the_whole_minimum():
    """$500 guaranteed, $300 actually sold → the host owes the $200 gap.

    This is the regression that matters: billing the full $500 here would
    charge the host for sales the truck already made.
    """
    calc = _mg(minimum=500, card=200, cash=100)
    assert calc["SUBTOTAL"] == 200.0
    assert calc["FINAL_INVOICE_AMOUNT"] == 200.0
    assert calc["MG_SHORTFALL"] == 200.0


def test_mg_counts_cash_toward_the_minimum():
    """Cash is half of 'what the truck took' — it must reduce the shortfall.

    Same card sales as above; the extra cash shrinks the gap pound for pound.
    """
    assert _mg(minimum=500, card=200, cash=100)["SUBTOTAL"] == 200.0
    assert _mg(minimum=500, card=200, cash=250)["SUBTOTAL"] == 50.0


def test_mg_raises_no_invoice_when_the_minimum_is_covered():
    """Sold past the minimum → nothing owed, and NO invoice at all.

    Not a $0 draft: that would sit in KonaOS for someone to chase.
    """
    calc = _mg(minimum=500, card=400, cash=200)   # 600 sold vs 500 minimum
    assert calc["SUBTOTAL"] == 0.0
    assert calc["FINAL_INVOICE_AMOUNT"] == 0.0
    assert calc["MG_SHORTFALL"] == 0.0

    payload = build_invoice_payload(
        {"EVENT_TYPE": "HYBRID", "calculations": calc}, {}, {}
    )
    assert payload is None, "a covered minimum must not produce an invoice draft"


def test_mg_exactly_meeting_the_minimum_raises_no_invoice():
    """The boundary: sales exactly equal to the minimum leave nothing owed."""
    calc = _mg(minimum=500, card=500, cash=0)
    assert calc["SUBTOTAL"] == 0.0
    assert calc["MG_SHORTFALL"] == 0.0


def test_mg_shortfall_invoice_includes_the_location_fee():
    """The location fee is owed on top of the gap when there IS a gap."""
    calc = _mg(minimum=500, card=300, cash=0, location_fee=75)
    assert calc["SUBTOTAL"] == 275.0   # 200 shortfall + 75 location fee


def test_mg_falls_back_to_the_full_minimum_while_cash_is_unknown():
    """Before cash is counted, sales are incomplete and a shortfall computed
    from them would be far too large.

    The pipeline defers MG invoices for exactly this reason, so this value
    should never reach a real invoice — but the fallback must be the safe
    direction (bill the minimum) rather than a wrong shortfall.
    """
    calc = _mg(minimum=500, card=300, cash=0, known=False)
    assert calc["SUBTOTAL"] == 500.0


# ── Cash overrides ──────────────────────────────────────────────────────────

def test_cash_override_survives_a_pipeline_rerun():
    """The regression that would silently corrupt the ledger.

    Cash is counted after the event, so the classifier's value (read from the
    driver's notes) is a guess. Once a real figure is posted, a later run must
    NOT recompute it from the notes — that would replace a counted till with
    whatever the driver did or didn't write down.
    """
    db = SessionLocal()
    try:
        run = PipelineRun(status="running", trigger="test",
                          filter_event_ids=["EVT-1003"])
        db.add(run)
        db.commit()
        db.refresh(run)
        run_pipeline(db, run)

        entry = (
            db.query(FinancialEntry)
            .filter(FinancialEntry.crm_event_id == "EVT-1003")
            .one()
        )

        # A human counts the till and posts a figure the notes never mentioned.
        overrides.set_override(entry, "cash_collected", 137.25,
                               source="manual", by="tester")
        overrides.recompute_cash_chain(entry)
        db.commit()

        # The nightly run comes around again.
        run2 = PipelineRun(status="running", trigger="test",
                           filter_event_ids=["EVT-1003"])
        db.add(run2)
        db.commit()
        db.refresh(run2)
        run_pipeline(db, run2)

        db.refresh(entry)
        assert entry.cash_collected == 137.25, (
            "the re-run overwrote counted cash with the classifier's value"
        )
        assert overrides.source_of(entry, "cash_collected") == "manual"
    finally:
        db.close()


def test_recompute_derives_tax_and_sales_from_cash():
    """Everything downstream of cash is derived, never separately editable —
    so there is no way to save a row whose tax doesn't follow from its cash."""
    entry = FinancialEntry(
        event_id=1, crm_event_id="X", event_type="selling",
        taxable=True, total_tax_rate=0.06,
        square_net_card=100.0, square_card_tax=6.0, square_tips_card=0.0,
        giveback_amount=0.0,
    )
    overrides.set_override(entry, "cash_collected", 106.0, source="api", by="bot")
    overrides.recompute_cash_chain(entry)

    # $106 gross at 6% → $6 tax, $100 net.
    assert entry.cash_tax == 6.0
    assert entry.cash_pre_tax == 100.0
    assert entry.event_sales_collected == 200.0   # 100 card + 100 cash, pre-tax
    assert entry.sales_tax == 12.0                # 6 card + 6 cash
    assert entry.sales_dollars == 212.0           # gross card + tips + gross cash


def test_tax_exempt_event_splits_no_cash_tax():
    """An exempt event's cash is all sales — none of it is tax."""
    entry = FinancialEntry(
        event_id=2, crm_event_id="Y", event_type="selling",
        taxable=False, total_tax_rate=0.06,
    )
    overrides.set_override(entry, "cash_collected", 106.0, source="api")
    overrides.recompute_cash_chain(entry)
    assert entry.cash_tax == 0.0
    assert entry.cash_pre_tax == 106.0


def test_clearing_an_override_hands_the_field_back_to_the_automation():
    entry = FinancialEntry(event_id=3, crm_event_id="Z", event_type="selling")
    overrides.set_override(entry, "cash_collected", 50.0, source="manual")
    assert overrides.source_of(entry, "cash_collected") == "manual"

    overrides.clear_override(entry, "cash_collected")
    assert overrides.get_override(entry, "cash_collected") is None
    # "ai" means the classifier's value stands again.
    assert overrides.source_of(entry, "cash_collected") == "ai"


def test_only_declared_fields_can_be_overridden():
    """A typo'd field name must fail loudly, not write a key nothing reads."""
    import pytest

    entry = FinancialEntry(event_id=4, crm_event_id="W")
    with pytest.raises(ValueError):
        overrides.set_override(entry, "square_net_card", 1.0, source="api")
    with pytest.raises(ValueError):
        overrides.set_override(entry, "cash_collected", 1.0, source="guesswork")


def test_min_guarantee_models_are_recognised():
    """The deferral and the shortfall pricing both hang off this check."""
    assert overrides.is_min_guarantee("MIN_GUARANTEE_HOURLY")
    assert overrides.is_min_guarantee("MIN_GUARANTEE_FLAT")
    assert overrides.is_min_guarantee("HYBRID_SELLING_PLUS_MIN_GUARANTEE")
    assert overrides.is_min_guarantee("min_guarantee_flat")  # case-insensitive
    assert not overrides.is_min_guarantee("SELLING_OPEN")
    assert not overrides.is_min_guarantee("")
    assert not overrides.is_min_guarantee(None)
