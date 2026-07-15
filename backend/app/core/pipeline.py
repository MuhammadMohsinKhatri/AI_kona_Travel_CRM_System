"""Pipeline orchestrator — the port of the n8n graph's control flow.

Restructured into batch phases so the UI can show live step-by-step progress:
fetch → clean/gate → classify → square → calculate → invoice → alerts → report.
Each phase updates ``run.progress`` (committed immediately) which the frontend
polls. Per-event failures are isolated: the event is marked errored and dropped
from the remaining phases without failing the run.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core import billing, event_cleaner, invoice_builder
from app.core.alerts import check_alerts
from app.core.equipment import map_equipment
from app.integrations import factory
from app.models import Alert, Event, Invoice, PipelineRun

NY = ZoneInfo("America/New_York")

# The visible steps, in order. Keep keys stable — the frontend keys off them.
PIPELINE_STEPS: list[tuple[str, str]] = [
    ("fetch", "Fetch events from Kona CRM"),
    ("clean", "Clean & filter events"),
    ("classify", "AI classification"),
    ("square", "Square reconciliation"),
    ("calculate", "Calculate invoices"),
    ("invoice", "Create invoice drafts"),
    ("alerts", "Check alerts & notify"),
    ("report", "Update monthly report"),
]


def _brand_key(brand: str) -> str:
    return "tom" if "tom" in (brand or "").lower() else "kona"


def _date_bounds_ms(date_str: str) -> tuple[Optional[int], Optional[int]]:
    """NY-local [00:00, 24:00) of YYYY-MM-DD as epoch-ms, for CRM date filters."""
    try:
        y, m, d = (int(x) for x in date_str.split("-"))
        start = datetime(y, m, d, tzinfo=NY)
        end = start + timedelta(days=1)
        return int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    except (ValueError, TypeError, AttributeError):
        return None, None


class _Progress:
    """Writes step statuses to run.progress and commits so pollers see them."""

    def __init__(self, db: Session, run: PipelineRun) -> None:
        self.db = db
        self.run = run
        run.progress = [
            {"key": k, "label": label, "status": "pending", "detail": ""}
            for k, label in PIPELINE_STEPS
        ]
        db.commit()

    def set(self, key: str, status: Optional[str] = None, detail: Optional[str] = None) -> None:
        steps = [dict(s) for s in (self.run.progress or [])]
        for s in steps:
            if s["key"] == key:
                if status is not None:
                    s["status"] = status
                if detail is not None:
                    s["detail"] = detail
        self.run.progress = steps
        self.db.commit()

    def counter(self, key: str, i: int, total: int) -> None:
        self.set(key, detail=f"{i}/{total}")


def run_pipeline(db: Session, run: PipelineRun) -> PipelineRun:
    """Execute one full pipeline pass, updating ``run`` in place."""
    crm = factory.get_crm()
    square = factory.get_square()
    classifier = factory.get_classifier()
    notifier = factory.get_notifier()

    log: list[str] = []
    progress = _Progress(db, run)

    def note(msg: str) -> None:
        log.append(f"{datetime.now(timezone.utc).isoformat()} {msg}")

    def drop_errored(items: list[dict], item: dict, exc: Exception, phase: str) -> None:
        db.rollback()
        run.events_errored += 1
        note(f"[{item['crm_id']}] ERROR in {phase}: {exc}")
        _mark_event_error(db, run, item["crm_id"], f"{phase}: {exc}")
        items.remove(item)

    try:
        # ── PHASE 1: FETCH ──────────────────────────────────────────────────
        progress.set("fetch", "running")
        from_ms, to_ms = (
            _date_bounds_ms(run.target_date) if run.target_date else (None, None)
        )
        summaries = crm.list_events(from_ms=from_ms, to_ms=to_ms)
        run.events_fetched = len(summaries)
        note(f"Fetched {len(summaries)} events from CRM")
        progress.set("fetch", "done", f"{len(summaries)} events")

        # ── PHASE 2: CLEAN & GATE ───────────────────────────────────────────
        progress.set("clean", "running")
        items: list[dict[str, Any]] = []
        skipped = 0
        filtered = 0
        for i, summary in enumerate(summaries, 1):
            crm_id = str(summary.get("id"))
            progress.counter("clean", i, len(summaries))
            try:
                raw = crm.get_event(crm_id) or summary
                cleaned = event_cleaner.clean_event(raw, brand_name=raw.get("brandName", ""))

                # Optional date filter: only process events on the target date.
                if run.target_date and cleaned.get("DATE") != run.target_date:
                    filtered += 1
                    note(f"[{crm_id}] filtered out — date {cleaned.get('DATE')} != {run.target_date}")
                    continue

                # Gate: booked/confirmed/completed, or pending with asset+staff.
                ok, reason = event_cleaner.is_processable(cleaned)
                if not ok:
                    run.events_skipped += 1
                    skipped += 1
                    note(f"[{crm_id}] skipped — {reason}")
                    _upsert_event(db, run, raw, cleaned, status="skipped")
                    db.commit()
                    continue
                note(f"[{crm_id}] accepted — {reason}")

                items.append({"crm_id": crm_id, "raw": raw, "cleaned": cleaned})
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                run.events_errored += 1
                note(f"[{crm_id}] ERROR in clean: {exc}")
                _mark_event_error(db, run, crm_id, f"clean: {exc}")
        detail = f"{len(items)} to process"
        if skipped:
            detail += f", {skipped} skipped"
        if filtered:
            detail += f", {filtered} off-date"
        progress.set("clean", "done", detail)

        # ── PHASE 3: CLASSIFY (LLM) ─────────────────────────────────────────
        from app.config import settings as _s

        progress.set("classify", "running")
        for i, item in enumerate(list(items), 1):
            progress.counter("classify", i, len(items))
            try:
                item["classification"] = classifier.classify(item["cleaned"])
                usage = item["classification"].get("_usage") or {}
                run.ai_prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
                run.ai_completion_tokens += int(usage.get("completion_tokens", 0) or 0)
            except Exception as exc:  # noqa: BLE001
                drop_errored(items, item, exc, "classify")
        run.ai_cost_usd = round(
            run.ai_prompt_tokens / 1e6 * _s.openai_input_cost_per_mtok
            + run.ai_completion_tokens / 1e6 * _s.openai_output_cost_per_mtok,
            4,
        )
        total_tok = run.ai_prompt_tokens + run.ai_completion_tokens
        classify_detail = f"{len(items)} classified"
        if total_tok:
            classify_detail += f" · {total_tok:,} tok · ${run.ai_cost_usd:.3f}"
        progress.set("classify", "done", classify_detail)

        # ── PHASE 4: SQUARE RECONCILIATION ──────────────────────────────────
        progress.set("square", "running")
        for i, item in enumerate(list(items), 1):
            progress.counter("square", i, len(items))
            try:
                equip = map_equipment(item["classification"])
                start_iso, end_iso = _event_window_utc(item["classification"], item["cleaned"])
                sq = square.search_orders(
                    brand=item["cleaned"].get("BRAND", ""),
                    device_id=equip.get("device_id"),
                    date_iso=item["cleaned"].get("DATE"),
                    start_iso=start_iso,
                    end_iso=end_iso,
                )
                sq["equipment"] = equip
                item["square"] = sq
            except Exception as exc:  # noqa: BLE001
                drop_errored(items, item, exc, "square")
        progress.set("square", "done", f"{len(items)} reconciled")

        # ── PHASE 5: CALCULATE ──────────────────────────────────────────────
        progress.set("calculate", "running")
        for i, item in enumerate(list(items), 1):
            progress.counter("calculate", i, len(items))
            try:
                calc = billing.calculate_invoice(item["classification"])
                item["calc"] = calc
                item["merged"] = {**item["classification"], "calculations": calc}
                item["event"] = _upsert_event(
                    db, run, item["raw"], item["cleaned"],
                    classification=item["classification"], square=item["square"],
                    calculations=calc, status="processed",
                )
                db.commit()
            except Exception as exc:  # noqa: BLE001
                drop_errored(items, item, exc, "calculate")
        total_amount = sum(i["calc"].get("FINAL_INVOICE_AMOUNT", 0) for i in items)
        progress.set("calculate", "done", f"${total_amount:,.2f} calculated")

        # ── PHASE 6: INVOICE DRAFTS ─────────────────────────────────────────
        from app.config import settings as _settings

        dry_run = _settings.pipeline_dry_run
        progress.set("invoice", "running", "dry-run" if dry_run else "")
        for i, item in enumerate(list(items), 1):
            progress.counter("invoice", i, len(items))
            try:
                payload = invoice_builder.build_invoice_payload(
                    item["merged"], item["cleaned"], item["raw"]
                )
                if payload:
                    if dry_run:
                        # Compute + store locally, but write NOTHING to the CRM.
                        _store_local_invoice(db, item["event"], payload, status="dry_run")
                        note(f"[{item['crm_id']}] DRY-RUN — would create draft "
                             f"${item['calc'].get('FINAL_INVOICE_AMOUNT')}")
                    else:
                        _replace_draft(db, crm, item["event"], payload, item["cleaned"])
                        crm.update_event(item["crm_id"], {
                            "EVENT_ID": item["crm_id"],
                            "invoiceAmount": item["calc"].get("FINAL_INVOICE_AMOUNT"),
                            "invoiceStatus": "draft",
                        })
                        note(f"[{item['crm_id']}] invoice draft created "
                             f"${item['calc'].get('FINAL_INVOICE_AMOUNT')}")
                    run.invoices_created += 1
                db.commit()
            except Exception as exc:  # noqa: BLE001
                drop_errored(items, item, exc, "invoice")
        progress.set(
            "invoice", "done",
            f"{run.invoices_created} drafts" + (" (dry-run)" if dry_run else ""),
        )

        # ── PHASE 7: ALERTS ─────────────────────────────────────────────────
        progress.set("alerts", "running")
        for i, item in enumerate(list(items), 1):
            progress.counter("alerts", i, len(items))
            try:
                alert_result = check_alerts(item["merged"])
                _save_alerts(db, item["event"], alert_result["alerts"])
                run.alerts_raised += len(alert_result["alerts"])
                if alert_result["hasAlerts"]:
                    item["event"].status = "needs_review"
                    notifier.send(alert_result["telegramMessage"])
                db.commit()
            except Exception as exc:  # noqa: BLE001
                drop_errored(items, item, exc, "alerts")
        progress.set("alerts", "done", f"{run.alerts_raised} raised")

        # ── PHASE 8: REPORT — financial ledger in Postgres ──────────────────
        # (Replaces the monthly Google Sheet; SheetsClient remains available
        # for an optional export but is no longer part of the pipeline.)
        progress.set("report", "running")
        for i, item in enumerate(list(items), 1):
            progress.counter("report", i, len(items))
            try:
                _upsert_financial_entry(db, run, item)
                run.events_processed += 1
                db.commit()
            except Exception as exc:  # noqa: BLE001
                drop_errored(items, item, exc, "report")
        progress.set("report", "done", f"{run.events_processed} ledger rows")

        run.status = "completed"
        run.finished_at = datetime.now(timezone.utc)
        note("Run completed")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        run.status = "failed"
        run.error = str(exc)
        run.finished_at = datetime.now(timezone.utc)
        note(f"Run FAILED {exc}")
        # Mark whatever step was running as errored so the UI shows where it died.
        steps = [dict(s) for s in (run.progress or [])]
        for s in steps:
            if s["status"] == "running":
                s["status"] = "error"
        run.progress = steps

    run.log = log
    db.commit()
    db.refresh(run)
    return run


# ── persistence helpers ──────────────────────────────────────────────────────

def _upsert_event(
    db: Session, run: PipelineRun, raw: dict[str, Any], cleaned: dict[str, Any],
    classification: dict[str, Any] | None = None,
    square: dict[str, Any] | None = None,
    calculations: dict[str, Any] | None = None,
    status: str = "processed",
) -> Event:
    crm_id = str(cleaned.get("EVENT_ID") or raw.get("id") or "")
    event = db.query(Event).filter(Event.crm_event_id == crm_id).one_or_none()
    if event is None:
        event = Event(crm_event_id=crm_id)
        db.add(event)

    event.event_code = cleaned.get("EVENT_CODE")
    event.event_name = cleaned.get("EVENT_NAME", "")
    event.brand = cleaned.get("BRAND", "")
    event.event_date = cleaned.get("DATE")
    event.final_status = cleaned.get("FINAL_EVENT_STATUS", "")
    event.raw = raw
    event.cleaned = cleaned
    event.status = status
    event.run_id = run.id
    event.error = None

    if classification is not None:
        event.classification = classification
        event.event_type = classification.get("EVENT_TYPE", "")
        event.billing_model = classification.get("BILLING_MODEL", "")
    if square is not None:
        event.square = square
    if calculations is not None:
        event.calculations = calculations
        event.final_invoice_amount = float(calculations.get("FINAL_INVOICE_AMOUNT", 0) or 0)

    db.flush()
    return event


def _store_local_invoice(
    db: Session, event: Event, payload: dict[str, Any],
    status: str = "draft", crm_invoice_id: str = "",
) -> Invoice:
    """Replace this event's locally-stored invoice rows with a fresh one."""
    db.query(Invoice).filter(Invoice.event_id == event.id).delete()
    invoice = Invoice(
        event_id=event.id,
        crm_invoice_id=crm_invoice_id or None,
        invoice_number=payload.get("invoiceNumber"),
        title=payload.get("title", ""),
        invoice_type=payload.get("invoiceType", "Invoice"),
        status=status,
        grand_total=float(payload.get("grandTotal", 0) or 0),
        subtotal=float(payload.get("subTotal", 0) or 0),
        tax_amount=float(payload.get("taxAmount", 0) or 0),
        due_amount=float(payload.get("dueAmount", 0) or 0),
        has_variance=bool(payload.get("_hasVariance")),
        variance_amount=float(payload.get("_varianceAmount", 0) or 0),
        payload=payload,
    )
    db.add(invoice)
    db.flush()
    return invoice


def _replace_draft(
    db: Session, crm, event: Event, payload: dict[str, Any], cleaned: dict[str, Any]
) -> None:
    """Delete any prior draft for this event, then create a fresh one."""
    existing = crm.list_invoices()
    event_code = payload.get("invoiceNumber")
    for inv in existing:
        if inv.get("eventId") == event.crm_event_id or inv.get("invoiceNumber") == event_code:
            inv_id = inv.get("invoiceId") or inv.get("id")
            if inv_id:
                crm.delete_invoice(str(inv_id))

    resp = crm.create_invoice(payload)
    _store_local_invoice(
        db, event, payload, status="draft",
        crm_invoice_id=str(resp.get("invoiceId") or resp.get("id") or ""),
    )


def _save_alerts(db: Session, event: Event, alerts: list[dict[str, str]]) -> None:
    db.query(Alert).filter(Alert.event_id == event.id).delete()
    for a in alerts:
        db.add(Alert(event_id=event.id, severity=a["severity"],
                     issue=a["issue"], action=a.get("action", "")))
    db.flush()


def _mark_event_error(db: Session, run: PipelineRun, crm_id: str, error: str) -> None:
    event = db.query(Event).filter(Event.crm_event_id == crm_id).one_or_none()
    if event is None:
        event = Event(crm_event_id=crm_id, run_id=run.id)
        db.add(event)
    event.status = "error"
    event.error = error
    db.commit()


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _r2(v: float) -> float:
    return round(v + 0.0, 2)


def _event_window_utc(cls: dict[str, Any], cleaned: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """Event's actual start/end (NY local) → UTC ISO, for the Square closed_at
    filter. Falls back to the cleaned start/end when actuals are absent."""
    from datetime import datetime

    def to_utc_iso(v: str) -> Optional[str]:
        if not v:
            return None
        s = str(v).strip().replace(" ", "T")
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
            try:
                dt = datetime.strptime(s[:26] if "." in s else s, fmt)
                return dt.replace(tzinfo=NY).astimezone(tz=timezone.utc).isoformat().replace("+00:00", "Z")
            except ValueError:
                continue
        return None

    start = to_utc_iso(cls.get("ACTUAL_EVENT_START_TIME") or cleaned.get("EVENT_STARTED"))
    end = to_utc_iso(cls.get("ACTUAL_EVENT_END_TIME") or cleaned.get("EVENT_ENDED"))
    return start, end


def _upsert_financial_entry(db: Session, run: PipelineRun, item: dict[str, Any]) -> None:
    """Write/refresh this event's row in the financial ledger (Postgres)."""
    from app.models import FinancialEntry

    cleaned = item["cleaned"]
    calc = item["calc"]
    cls = item["classification"]
    sq = item.get("square") or {}
    event: Event = item["event"]

    entry = (
        db.query(FinancialEntry)
        .filter(FinancialEntry.event_id == event.id)
        .one_or_none()
    )
    if entry is None:
        entry = FinancialEntry(event_id=event.id)
        db.add(entry)

    subtotal = _num(calc.get("SUBTOTAL"))
    sales_tax = _num(calc.get("SALES_TAX"))
    invoice_total = _num(calc.get("FINAL_INVOICE_AMOUNT"))
    payment_method = str(calc.get("PAYMENT_METHOD", ""))
    taxable = str(cls.get("TAXABLE", "YES")).upper() == "YES"
    cash_collected = _num(cls.get("CASH_COLLECTED_AMOUNT"))
    tax_rate = _num(calc.get("TAX_RATE"))
    sq_breakdown = sq.get("breakdown") or {}

    # Split staff names into worker 1/2 (hours per worker aren't in the CRM feed;
    # use the event's total hours as the best available value).
    staff = [s.strip() for s in (cleaned.get("STAFF_ASSIGNED") or "").split(",") if s.strip()]
    total_hours = _num(cls.get("TOTAL_EVENT_HOURS"))

    entry.run_id = run.id
    entry.month = (cleaned.get("DATE") or "")[:7] or None
    # identity
    entry.event_date = cleaned.get("DATE")
    entry.crm_event_id = event.crm_event_id
    entry.event_name = cleaned.get("EVENT_NAME", "")
    entry.event_code = cleaned.get("EVENT_CODE")
    entry.brand = cleaned.get("BRAND", "")
    entry.final_status = cleaned.get("FINAL_EVENT_STATUS", "")
    entry.event_type = str(cls.get("EVENT_TYPE", ""))
    entry.billing_model = str(cls.get("BILLING_MODEL", ""))
    # square
    entry.square_gross_sales = _num(sq_breakdown.get("gross_sales"))
    entry.square_discounts = _num(sq_breakdown.get("discounts"))
    entry.square_net_card = _num(sq_breakdown.get("net_card"))
    entry.square_card_tax = _num(sq_breakdown.get("card_tax"))
    entry.square_tips_card = _num(sq_breakdown.get("tips_card"))
    entry.square_cc_fee = _num(sq_breakdown.get("cc_fee"))
    entry.square_orders = int(sq.get("order_count") or 0)
    entry.square_device = sq.get("device_id")
    entry.square_sales = _num(sq_breakdown.get("net_card")) or _num(sq.get("total_collected"))
    # cash
    entry.cash_collected = cash_collected
    entry.cash_tax = _r2(cash_collected - cash_collected / (1 + tax_rate)) if (taxable and cash_collected) else 0.0
    entry.cash_pre_tax = _r2(cash_collected - entry.cash_tax) if cash_collected else 0.0
    # billing
    entry.check_invoice = invoice_total if payment_method == "CHECK" else 0.0
    entry.deposit = _num(cls.get("DEPOSIT_AMOUNT"))
    entry.taxable = taxable
    entry.event_sales_collected = _r2(cash_collected + entry.square_net_card)
    entry.sales_tax = sales_tax
    entry.sales_dollars = _num(calc.get("SALES_AMOUNT")) or subtotal
    entry.giveback_amount = _num(calc.get("GIVEBACK_AMOUNT"))
    entry.net_event_sales = _r2(subtotal - _num(calc.get("GIVEBACK_AMOUNT")))
    entry.location_fee = _num(cls.get("LOCATION_FEE"))
    # workflow
    entry.paid = str(cls.get("PAID_STATUS") or "").upper() in ("TRUE", "PAID", "YES", "1")
    # staff
    entry.worker_1 = staff[0] if len(staff) > 0 else ""
    entry.worker_1_hours = total_hours if staff else 0.0
    entry.worker_2 = staff[1] if len(staff) > 1 else ""
    entry.worker_2_hours = total_hours if len(staff) > 1 else 0.0
    entry.hours_paid = entry.hours_paid or False  # manual flag, preserved across runs
    # notes/flags
    entry.note = str(cls.get("NOTE", ""))
    entry.invoice_drafted = invoice_total > 0 and str(cls.get("EVENT_TYPE", "")).lower() in ("invoice", "hybrid")
    entry.invoice_sent = entry.invoice_sent or False  # manual flag, preserved
    # classifier / calc
    entry.total_event_hours = total_hours
    entry.attendee_count = int(_num(cls.get("ATTENDEE_COUNT")))
    entry.base_amount = _num(cls.get("BASE_AMOUNT"))
    entry.hourly_rate = _num(cls.get("HOURLY_RATE"))
    entry.rate_per_serving = _num(cls.get("RATE_PER_SERVING"))
    entry.host_covers_shortfall = str(cls.get("BILLING_MODEL", "")).upper().startswith(("MIN_GUARANTEE", "HYBRID_SELLING_PLUS_MIN"))
    entry.units_served = _num(cls.get("UNITS_SERVED_TOTAL"))
    entry.units_included = _num(cls.get("UNITS_INCLUDED_IN_BASE"))
    entry.payment_method = payment_method
    entry.tax_mode = "EXEMPT" if not taxable else "TAXABLE"
    entry.subtotal = subtotal
    entry.actual_sales = _num(sq_breakdown.get("net_card")) or cash_collected
    entry.mg_shortfall = _num(calc.get("MG_SHORTFALL"))
    entry.total_tax_rate = tax_rate
    entry.total_tax = _num(calc.get("TOTAL_TAX"))
    # rollups
    entry.cc_fee = _num(calc.get("CC_FEE"))
    entry.invoice_total = invoice_total
    entry.balance_due = _num(calc.get("BALANCE_DUE"))
    entry.has_variance = bool(calc.get("HAS_VARIANCE"))
    entry.variance_amount = _num(calc.get("VARIANCE_AMOUNT"))

    db.flush()
