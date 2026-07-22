"""End-to-end pipeline test against the mock integrations + SQLite."""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_konaice.db")
os.environ.setdefault("PIPELINE_RUN_INLINE", "true")
os.environ.setdefault("MOCK_LATENCY_S", "0")  # no simulated latency in tests
# Force mock providers regardless of .env — tests must NEVER touch production.
os.environ["CRM_PROVIDER"] = "mock"
os.environ["SQUARE_PROVIDER"] = "mock"
os.environ["OPENAI_PROVIDER"] = "mock"
os.environ["TELEGRAM_PROVIDER"] = "mock"
os.environ["PIPELINE_DRY_RUN"] = "false"

from app.core.pipeline import run_pipeline  # noqa: E402
from app.db.base import Base, SessionLocal, engine  # noqa: E402
from app.models import Alert, Event, FinancialEntry, Invoice, PipelineRun  # noqa: E402


def setup_module(_):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_selling_events_default_to_card_payment():
    """Selling events settle via Square — a defaulted CHECK becomes card;
    an explicit CASH (driver wrote it) is kept; invoice events untouched."""
    from app.core.pipeline import _normalize_classification as norm

    assert norm({"EVENT_TYPE": "selling", "PAYMENT_METHOD": "CHECK"})["PAYMENT_METHOD"] == "CREDIT_CARD"
    assert norm({"EVENT_TYPE": "selling", "PAYMENT_METHOD": ""})["PAYMENT_METHOD"] == "CREDIT_CARD"
    assert norm({"EVENT_TYPE": "selling", "PAYMENT_METHOD": "CASH"})["PAYMENT_METHOD"] == "CASH"
    assert norm({"EVENT_TYPE": "invoice", "PAYMENT_METHOD": "CHECK"})["PAYMENT_METHOD"] == "CHECK"


def test_error_summary_is_human_readable():
    """The CRM Activity error row must say what was being attempted and why it
    failed, in plain language — not just echo the raw exception."""
    from app.core.pipeline import _error_summary

    s = _error_summary("invoice", RuntimeError(
        'KonaOS API error 500: {"messageCode":"main.internalServerError"}'))
    assert "syncing financials" in s.lower()
    assert "500" in s

    s2 = _error_summary("square", RuntimeError("connection timed out"))
    assert "Square reconciliation" in s2
    assert "timeout" in s2.lower()


def test_crm_write_failure_is_recorded_on_crm_activity():
    """When a KonaOS write fails mid-run, the event is marked errored AND an
    `error` row lands on the CRM Activity trail with a readable reason — so a
    failure is visible there, not only in the raw run log."""
    from unittest.mock import patch

    from app.integrations.mocks import MockCRMClient
    from app.models import CrmAuditEntry

    orig = MockCRMClient.update_event

    def boom(self, event_id, payload):
        # EVT-1003 is a selling event → hits the financial sync → make it 500.
        if event_id == "EVT-1003":
            raise RuntimeError(
                'KonaOS API error 500: {"messageCode":"main.internalServerError"}')
        return orig(self, event_id, payload)

    db = SessionLocal()
    try:
        run = PipelineRun(status="running", trigger="test")
        db.add(run)
        db.commit()
        db.refresh(run)

        with patch.object(MockCRMClient, "update_event", boom):
            run_pipeline(db, run)

        ev = db.query(Event).filter(Event.crm_event_id == "EVT-1003").one()
        assert ev.status == "error"
        assert ev.error and "500" in ev.error

        audit = (
            db.query(CrmAuditEntry)
            .filter(CrmAuditEntry.crm_event_id == "EVT-1003",
                    CrmAuditEntry.action == "error")
            .all()
        )
        assert len(audit) == 1
        assert "Failed while" in audit[0].summary
        assert "500" in audit[0].summary or "internal server" in audit[0].summary.lower()
        assert audit[0].detail.get("phase") == "invoice"

        # The raw run log ends with a human-readable roll-up that names the
        # errored event under an ERRORED section.
        log_text = "\n".join(run.log or [])
        assert "RUN SUMMARY" in log_text
        assert "ERRORED" in log_text and "EVT-1003" in log_text
        assert "PROCESSED" in log_text
    finally:
        db.close()


def test_run_scoped_to_specific_event_ids():
    """A specific-event run processes only the given CRM ids, date-independent."""
    db = SessionLocal()
    try:
        run = PipelineRun(status="running", trigger="test",
                          filter_event_ids=["EVT-1003"])
        db.add(run)
        db.commit()
        db.refresh(run)

        run_pipeline(db, run)

        assert run.status == "completed"
        assert run.events_fetched == 1  # only the one requested id was fetched
        touched = db.query(Event).filter(Event.run_id == run.id).all()
        assert {e.crm_event_id for e in touched} == {"EVT-1003"}
    finally:
        db.close()


def test_run_filtered_by_event_type():
    """A type-filtered run classifies the whole date but only fully processes
    the selected EVENT_TYPE(s); the rest are marked skipped with a reason."""
    db = SessionLocal()
    try:
        run = PipelineRun(status="running", trigger="test",
                          filter_event_types=["selling"])
        db.add(run)
        db.commit()
        db.refresh(run)

        run_pipeline(db, run)

        assert run.status == "completed"
        processed = db.query(Event).filter(
            Event.run_id == run.id, Event.status == "processed"
        ).all()
        # Everything that fully processed is a selling event.
        assert processed, "expected at least one selling event to process"
        assert all(e.event_type == "selling" for e in processed)
        # At least one non-selling event was set aside by the type filter.
        type_skipped = db.query(Event).filter(
            Event.run_id == run.id, Event.status == "skipped",
            Event.status_reason.like("filtered out%"),
        ).all()
        assert type_skipped, "expected non-selling events to be filtered out"
    finally:
        db.close()


def test_full_pipeline_runs_end_to_end():
    db = SessionLocal()
    try:
        run = PipelineRun(status="running", trigger="test")
        db.add(run)
        db.commit()
        db.refresh(run)

        run_pipeline(db, run)

        assert run.status == "completed"
        assert run.events_fetched == 6
        # Cancelled event is skipped by the confirmed/completed gate.
        assert run.events_skipped >= 1
        assert run.events_processed >= 4

        events = db.query(Event).all()
        assert len(events) == 6

        # The base-fee + servings sample should produce an invoice.
        lincoln = db.query(Event).filter(Event.crm_event_id == "EVT-1001").one()
        assert lincoln.billing_model == "INVOICE_BASE_FEE_PLUS_SERVINGS"
        assert lincoln.final_invoice_amount > 0
        assert db.query(Invoice).filter(Invoice.event_id == lincoln.id).count() == 1

        # The incomplete corporate event should raise alerts and need review.
        corp = db.query(Event).filter(Event.crm_event_id == "EVT-1005").one()
        assert corp.status == "needs_review"
        assert db.query(Alert).filter(Alert.event_id == corp.id).count() >= 1

        # Financial ledger (Postgres, replaces the Google Sheet): one row per
        # processed event, carrying the calculated totals + Square data.
        entries = db.query(FinancialEntry).all()
        assert len(entries) == run.events_processed
        lincoln_entry = (
            db.query(FinancialEntry).filter(FinancialEntry.event_id == lincoln.id).one()
        )
        assert lincoln_entry.invoice_total == lincoln.final_invoice_amount
        assert lincoln_entry.month == lincoln.event_date[:7]
        # Invoice events are host-billed — Square reconciliation is skipped,
        # so no card sales may be attributed to them.
        assert lincoln_entry.square_sales == 0
        assert lincoln_entry.square_gross_sales == 0
        assert lincoln_entry.square_orders == 0
        # Invoice (host-billed) events: Event Sales Collected and Net Event
        # Sales both equal the Check / Invoice (billed) amount.
        billed = lincoln_entry.check_invoice or lincoln_entry.invoice_total
        assert billed > 0
        assert lincoln_entry.event_sales_collected == billed
        assert lincoln_entry.net_event_sales == billed

        # Selling events: derived columns follow the legacy sheet formulas.
        popup = db.query(Event).filter(Event.crm_event_id == "EVT-1003").one()
        pe = db.query(FinancialEntry).filter(FinancialEntry.event_id == popup.id).one()
        assert pe.event_sales_collected == round(pe.square_net_card + pe.cash_pre_tax, 2)
        assert pe.sales_tax == round(pe.square_card_tax + pe.cash_tax, 2)
        assert pe.sales_dollars == round(
            pe.square_net_card + pe.square_card_tax + pe.square_tips_card + pe.cash_collected, 2
        )
        assert pe.net_event_sales == round(pe.event_sales_collected - pe.giveback_amount, 2)
    finally:
        db.close()
