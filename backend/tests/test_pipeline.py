"""End-to-end pipeline test against the mock integrations + SQLite."""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_konaice.db")
os.environ.setdefault("PIPELINE_RUN_INLINE", "true")
os.environ.setdefault("MOCK_LATENCY_S", "0")  # no simulated latency in tests
# Force mock providers regardless of .env — tests must NEVER touch production.
os.environ["CRM_PROVIDER"] = "mock"
os.environ["SQUARE_PROVIDER"] = "mock"
os.environ["OPENAI_PROVIDER"] = "mock"
os.environ["SHEETS_PROVIDER"] = "mock"
os.environ["TELEGRAM_PROVIDER"] = "mock"
os.environ["PIPELINE_DRY_RUN"] = "false"

from app.core.pipeline import run_pipeline  # noqa: E402
from app.db.base import Base, SessionLocal, engine  # noqa: E402
from app.models import Alert, Event, FinancialEntry, Invoice, PipelineRun  # noqa: E402


def setup_module(_):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


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
        assert lincoln_entry.square_sales >= 0
    finally:
        db.close()
