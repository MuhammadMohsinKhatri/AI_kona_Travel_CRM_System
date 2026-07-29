"""Rename stored EVENT_TYPE "invoice" -> "package" and INVOICE_* models -> PACKAGE_*.

The application does NOT need this to run. Legacy values are aliased at read time
(billing.canonical_event_type / canonical_billing_model), so historical events keep
pricing and keep drafting invoices either way. This only makes the stored rows —
and therefore the Events, Financials and event-detail screens — show the current
vocabulary instead of a mix of old and new.

Touches three tables: events, financial_entries, and the JSON classification blob
on events (the blob is what the detail page and any re-calculation read).

    cd backend
    python scripts/migrate_invoice_to_package.py            # dry run, prints counts
    python scripts/migrate_invoice_to_package.py --apply    # writes

Safe to re-run: every statement is idempotent, matching only pre-rename values.
Take a database snapshot first regardless — this rewrites financial rows.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("CRM_PROVIDER", "mock")
os.environ.setdefault("SQUARE_PROVIDER", "mock")
os.environ.setdefault("OPENAI_PROVIDER", "mock")
os.environ.setdefault("TELEGRAM_PROVIDER", "mock")

MODEL_RENAMES = {
    "INVOICE_PER_SERVING": "PACKAGE_PER_SERVING",
    "INVOICE_BASE_FEE_PLUS_SERVINGS": "PACKAGE_BASE_FEE_PLUS_SERVINGS",
    "INVOICE_FIXED_PACKAGE": "PACKAGE_FIXED",
    "INVOICE_HOURLY": "PACKAGE_HOURLY",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="write the changes (without this it only reports counts)")
    args = ap.parse_args()

    from sqlalchemy import func, select

    from app.db.base import SessionLocal
    from app.models import Event, FinancialEntry

    db = SessionLocal()
    try:
        def count(model, column, value):
            return db.execute(
                select(func.count()).select_from(model).where(column == value)
            ).scalar() or 0

        planned: list[tuple[str, int]] = []

        et_events = count(Event, Event.event_type, "invoice")
        et_ledger = count(FinancialEntry, FinancialEntry.event_type, "invoice")
        planned.append(("events.event_type       'invoice' -> 'package'", et_events))
        planned.append(("financial_entries.event_type 'invoice' -> 'package'", et_ledger))

        for legacy, current in MODEL_RENAMES.items():
            planned.append(
                (f"events.billing_model {legacy} -> {current}",
                 count(Event, Event.billing_model, legacy)))
            planned.append(
                (f"financial_entries.billing_model {legacy} -> {current}",
                 count(FinancialEntry, FinancialEntry.billing_model, legacy)))

        # The JSON classification blob is read by the detail page and by any
        # recalculation, so a row is only fully migrated once the blob matches.
        blobs = [
            e for e in db.execute(select(Event)).scalars()
            if isinstance(e.classification, dict) and (
                str(e.classification.get("EVENT_TYPE", "")).lower() == "invoice"
                or str(e.classification.get("BILLING_MODEL", "")) in MODEL_RENAMES
            )
        ]
        planned.append(("events.classification JSON blob", len(blobs)))

        total = sum(n for _, n in planned)
        print()
        for label, n in planned:
            print(f"  {n:>6}  {label}")
        print(f"\n  {total} row update(s) pending\n")

        if total == 0:
            print("  Nothing to do — already migrated.\n")
            return 0
        if not args.apply:
            print("  Dry run. Re-run with --apply to write.\n")
            return 0

        db.query(Event).filter(Event.event_type == "invoice").update(
            {Event.event_type: "package"}, synchronize_session=False)
        db.query(FinancialEntry).filter(FinancialEntry.event_type == "invoice").update(
            {FinancialEntry.event_type: "package"}, synchronize_session=False)
        for legacy, current in MODEL_RENAMES.items():
            db.query(Event).filter(Event.billing_model == legacy).update(
                {Event.billing_model: current}, synchronize_session=False)
            db.query(FinancialEntry).filter(
                FinancialEntry.billing_model == legacy).update(
                {FinancialEntry.billing_model: current}, synchronize_session=False)

        for event in blobs:
            blob = dict(event.classification)
            if str(blob.get("EVENT_TYPE", "")).lower() == "invoice":
                blob["EVENT_TYPE"] = "package"
            model = str(blob.get("BILLING_MODEL", ""))
            if model in MODEL_RENAMES:
                blob["BILLING_MODEL"] = MODEL_RENAMES[model]
            # Reassign rather than mutate: SQLAlchemy only detects a JSON change
            # when the attribute itself is replaced.
            event.classification = blob

        db.commit()
        print("  Applied.\n")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
