"""Pipeline orchestrator — the port of the n8n graph's control flow.

Restructured into batch phases so the UI can show live step-by-step progress:
fetch → clean/gate → classify → square → calculate → invoice → alerts → report.
Each phase updates ``run.progress`` (committed immediately) which the frontend
polls. Per-event failures are isolated: the event is marked errored and dropped
from the remaining phases without failing the run.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
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
        event = _mark_event_error(db, run, item["crm_id"], f"{phase}: {exc}")
        # Also record the failure on the CRM Activity trail, so an errored
        # event is visible there alongside the successful writes — not only in
        # the raw run log. This is the row a "why didn't this sync?" question
        # gets answered from.
        detail = {"phase": phase, "error": str(exc)}
        # DIAGNOSTIC: if the CRM client attached the exact body it PUT and the
        # raw KonaOS response (see KonaosClient.update_event), carry them into
        # the audit detail so a failing event can be diffed against a working
        # one from the CRM Activity page — no SSH needed.
        attempted = getattr(exc, "attempted_payload", None)
        konaos_response = getattr(exc, "konaos_response", None)
        if attempted is not None:
            detail["attempted_payload"] = attempted
        if konaos_response is not None:
            detail["konaos_response"] = str(konaos_response)[:4000]
        _audit(db, event, "error", _error_summary(phase, exc),
               detail=detail, run_id=run.id)
        db.commit()
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
                    _upsert_event(db, run, raw, cleaned, status="skipped",
                                  status_reason=reason)
                    db.commit()
                    continue
                note(f"[{crm_id}] accepted — {reason}")

                items.append({"crm_id": crm_id, "raw": raw, "cleaned": cleaned,
                              "gate_reason": reason})
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                run.events_errored += 1
                note(f"[{crm_id}] ERROR in clean: {exc}")
                event = _mark_event_error(db, run, crm_id, f"clean: {exc}")
                _audit(db, event, "error", _error_summary("clean", exc),
                       detail={"phase": "clean", "error": str(exc)}, run_id=run.id)
                db.commit()
        detail = f"{len(items)} to process"
        if skipped:
            detail += f", {skipped} skipped"
        if filtered:
            detail += f", {filtered} off-date"
        progress.set("clean", "done", detail)

        # ── PHASE 3: CLASSIFY (LLM) ─────────────────────────────────────────
        from app.config import settings as _s

        progress.set("classify", "running")
        from app.core.rule_classifier import try_rule_classify

        rule_classified = 0
        for i, item in enumerate(list(items), 1):
            progress.counter("classify", i, len(items))
            try:
                # Code first: form-generated structured notes parse exactly
                # (no cost, no model variance). Free-text notes go to the LLM.
                classification = try_rule_classify(item["cleaned"])
                if classification is not None:
                    rule_classified += 1
                else:
                    classification = classifier.classify(item["cleaned"])
                item["classification"] = _normalize_classification(classification)
                usage = item["classification"].get("_usage") or {}
                run.ai_prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
                run.ai_completion_tokens += int(usage.get("completion_tokens", 0) or 0)
                # Recompute cost each iteration so a still-running run shows a
                # live AI cost (not just tokens) instead of "—" until it ends.
                run.ai_cost_usd = round(
                    run.ai_prompt_tokens / 1e6 * _s.openai_input_cost_per_mtok
                    + run.ai_completion_tokens / 1e6 * _s.openai_output_cost_per_mtok,
                    4,
                )
            except Exception as exc:  # noqa: BLE001
                drop_errored(items, item, exc, "classify")
        total_tok = run.ai_prompt_tokens + run.ai_completion_tokens
        classify_detail = f"{len(items)} classified"
        if rule_classified:
            classify_detail += f" · {rule_classified} rule-based (no AI)"
        if total_tok:
            classify_detail += f" · {total_tok:,} tok · ${run.ai_cost_usd:.3f}"
        progress.set("classify", "done", classify_detail)

        # ── PHASE 4: SQUARE RECONCILIATION ──────────────────────────────────
        progress.set("square", "running")
        sq_skipped = 0
        for i, item in enumerate(list(items), 1):
            progress.counter("square", i, len(items))
            try:
                equip = map_equipment(item["classification"])
                # Pure invoice events are host-billed — guests never pay via
                # Square, so attributing card sales to them is wrong (n8n
                # routed invoice events around the Square search entirely).
                # Selling, hybrid, and minimum-guarantee events still reconcile.
                event_type = str(item["classification"].get("EVENT_TYPE", "")).strip().lower()
                if event_type == "invoice":
                    item["square"] = {
                        "brand": item["cleaned"].get("BRAND", ""),
                        "device_id": equip.get("device_id"),
                        "order_count": 0, "total_collected": 0.0,
                        "payment_ids": [], "breakdown": {},
                        "equipment": equip,
                        "note": "skipped — invoice event (host-billed, no Square attribution)",
                    }
                    sq_skipped += 1
                    continue
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
        sq_detail = f"{len(items) - sq_skipped} reconciled"
        if sq_skipped:
            sq_detail += f", {sq_skipped} invoice events skipped"
        progress.set("square", "done", sq_detail)

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
                    status_reason=item.get("gate_reason", ""),
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
        crm_synced = 0
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
                        run.invoices_created += 1
                    else:
                        outcome = _replace_draft(
                            db, crm, item["event"], payload, item["cleaned"],
                            note=note, run_id=run.id,
                        )
                        if outcome == "created":
                            note(f"[{item['crm_id']}] invoice draft created "
                                 f"${item['calc'].get('FINAL_INVOICE_AMOUNT')}")
                            run.invoices_created += 1
                            # Reflect the amount onto the event — best-effort:
                            # the draft already exists in KonaOS, so a sync
                            # failure must not mark this event as errored
                            # (that's how created drafts got reported as
                            # failures and re-runs found "surprise" invoices).
                            # invoiceStatus is deliberately NOT sent — it's
                            # read-only on the event PUT (see client.update_event).
                            try:
                                sync_result = crm.update_event(item["crm_id"], {
                                    "EVENT_ID": item["crm_id"],
                                    "invoiceAmount": item["calc"].get("FINAL_INVOICE_AMOUNT"),
                                })
                                note(f"[{item['crm_id']}] invoice amount synced to KonaOS "
                                     f"event{_equip_suffix(sync_result)}")
                                _audit(
                                    db, item["event"], "event_updated",
                                    "Synced invoice amount to KonaOS event"
                                    + _equip_suffix(sync_result),
                                    detail={
                                        "fields_updated": ["invoiceAmount"],
                                        "values": {
                                            "invoiceAmount": item["calc"].get("FINAL_INVOICE_AMOUNT"),
                                        },
                                        **_preserved_detail(sync_result),
                                    },
                                    run_id=run.id,
                                )
                            except Exception as sync_exc:  # noqa: BLE001
                                note(f"[{item['crm_id']}] WARNING — draft created but "
                                     f"event sync failed: {sync_exc}")
                        else:
                            note(f"[{item['crm_id']}] invoice {outcome}")

                # ── CRM FINANCIAL SYNC (n8n "update event2/3") ────────────────
                # Non-invoice events (selling / hybrid / MG) get their actuals
                # written back onto the KonaOS event: card amount, tax rate,
                # tips, giveback. Pure invoice events are excluded — their
                # Square data is intentionally empty and would zero out real
                # CRM values. The KonaOS client PUTs read-modify-write, so
                # everything else on the event is preserved.
                event_type = str(item["classification"].get("EVENT_TYPE", "")).strip().lower()
                if event_type != "invoice":
                    sq_bd = (item.get("square") or {}).get("breakdown") or {}
                    calc = item["calc"]
                    cls = item["classification"]
                    net_card = _num(sq_bd.get("net_card"))
                    card_tax = _num(sq_bd.get("card_tax"))
                    tips = _num(sq_bd.get("tips_card"))
                    giveback = _num(calc.get("GIVEBACK_AMOUNT"))
                    tax_rate = _num(calc.get("TAX_RATE"))
                    cash = _num(cls.get("CASH_COLLECTED_AMOUNT"))
                    cash_pre_tax = _r2(cash / (1 + tax_rate)) if (tax_rate and cash) else cash
                    collected = _r2(net_card + cash_pre_tax)
                    financials = {
                        "EVENT_ID": item["crm_id"],
                        "ccAmount": _r2(net_card + card_tax),
                        "taxPercent": tax_rate,
                        "tipAmount": _r2(tips),
                        "giveback": _r2(giveback),
                        "givebackPercentage": _r2(giveback / collected * 100) if collected > 0 else 0,
                    }
                    if dry_run:
                        note(f"[{item['crm_id']}] DRY-RUN — would update KonaOS event "
                             f"(card ${financials['ccAmount']}, tips ${financials['tipAmount']}, "
                             f"giveback ${financials['giveback']})")
                    else:
                        sync_result = crm.update_event(item["crm_id"], financials)
                        crm_synced += 1
                        note(f"[{item['crm_id']}] KonaOS event updated — "
                             f"card ${financials['ccAmount']}, tips ${financials['tipAmount']}, "
                             f"giveback ${financials['giveback']}{_equip_suffix(sync_result)}")
                        _audit(
                            db, item["event"], "event_updated",
                            f"Synced financial actuals — card ${financials['ccAmount']}, "
                            f"tips ${financials['tipAmount']}, giveback ${financials['giveback']}"
                            + _equip_suffix(sync_result),
                            detail={
                                "fields_updated": sorted(k for k in financials if k != "EVENT_ID"),
                                "values": {k: v for k, v in financials.items() if k != "EVENT_ID"},
                                **_preserved_detail(sync_result),
                            },
                            run_id=run.id,
                        )
                db.commit()
            except Exception as exc:  # noqa: BLE001
                drop_errored(items, item, exc, "invoice")
        invoice_detail = f"{run.invoices_created} drafts"
        if crm_synced:
            invoice_detail += f" · {crm_synced} CRM events updated"
        if dry_run:
            invoice_detail += " (dry-run)"
        progress.set("invoice", "done", invoice_detail)

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
        # This IS the monthly Google Sheet's replacement: one row per event in
        # financial_entries, read by the Financials tab and its filtered CSV
        # export. Nothing is written to Google Sheets.
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

        # A human-readable roll-up appended to the run log itself, so the raw
        # log is self-contained: which events errored (and why), which were
        # skipped (and why), which processed (with type + billing model). The
        # Runs page and Dashboard render the same breakdown visually; this is
        # the text version for anyone reading the log directly.
        _append_run_summary(db, run, note)

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
    status_reason: str = "",
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
    event.status_reason = status_reason
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


def _audit(
    db: Session, event: Event, action: str, summary: str,
    detail: dict[str, Any] | None = None, run_id: Optional[int] = None,
) -> None:
    """Persist one row of the structured CRM-write audit trail (see
    CrmAuditEntry) — the browsable/filterable record behind the "CRM
    Activity" page and each event's own activity history, as opposed to the
    per-run text log which is what ``note`` writes."""
    from app.models import CrmAuditEntry

    db.add(CrmAuditEntry(
        event_id=event.id, crm_event_id=event.crm_event_id,
        event_name=event.event_name, event_date=event.event_date, run_id=run_id,
        action=action, summary=summary, detail=detail or {},
    ))


def _replace_draft(
    db: Session, crm, event: Event, payload: dict[str, Any], cleaned: dict[str, Any],
    note: Callable[[str], None] = lambda msg: None, run_id: Optional[int] = None,
) -> str:
    """Create this event's invoice, unless it already has one.

    MAXIMALLY CONSERVATIVE as of the 2026-07-21 incident: two invoices that
    KonaOS confirmed as genuinely "sent"/"paid" (00671, 00675) vanished after
    a pipeline re-run, meaning the previous "only replace a literal 'draft'
    status" rule was not a reliable enough guard — the exact mechanism is
    still unconfirmed. Until that's root-caused, ANY invoice matching this
    event (by eventId or invoiceNumber) — including one whose status parses
    as "draft" — is left completely untouched: never deleted, never
    replaced. Only when ZERO invoices match does this create a new one.
    This intentionally also disables the "waive CC fee → replace the draft"
    auto-replacement (that route shares this function): a match now always
    means "skip, flag for manual review" instead of "inspect status and
    maybe delete." Revisit once the invoice-matching bug is confirmed fixed.

    Returns "created", or "skipped — …" when a match exists.

    Every decision is written to the run log via ``note`` (which invoices
    matched, their status) AND to the structured CrmAuditEntry table via
    ``_audit``, so a run can be audited after the fact from either the raw
    log or the filterable CRM Activity page — this is what a client dispute
    over "where did my invoice go" gets checked against.
    """
    eid = event.crm_event_id
    existing = crm.list_invoices()
    event_code = payload.get("invoiceNumber")
    matches = [
        inv for inv in existing
        if inv.get("eventId") == eid or inv.get("invoiceNumber") == event_code
    ]
    note(f"[{eid}] invoice check — {len(existing)} invoice(s) in the live KonaOS "
         f"list, {len(matches)} matching this event (by eventId or invoiceNumber "
         f"'{event_code}')")

    if matches:
        refs = []
        for inv in matches:
            status = str(inv.get("invoiceStatus") or inv.get("status") or "").strip().lower()
            inv_ref = inv.get("invoiceNumber") or inv.get("invoiceId") or inv.get("id") or "?"
            refs.append(f"{inv_ref} ({status or 'unknown'})")
        summary = f"existing invoice(s) {', '.join(refs)} — PROTECTED, not deleted or replaced"
        note(f"[{eid}] {summary}")
        _audit(
            db, event, "invoice_skipped",
            f"Existing invoice(s) found: {', '.join(refs)} — protected, not replaced "
            "(conservative hold pending invoice-matching bug fix)",
            detail={"matches": refs}, run_id=run_id,
        )
        return f"skipped — {summary}"

    resp = crm.create_invoice(payload)
    new_id = str(resp.get("invoiceId") or resp.get("id") or "")
    note(f"[{eid}] created invoice {payload.get('invoiceNumber')} "
         f"(KonaOS id {new_id or 'unknown'}) — ${payload.get('grandTotal')}")
    _audit(
        db, event, "invoice_created",
        f"Created invoice {payload.get('invoiceNumber')} — ${payload.get('grandTotal')}",
        detail={
            "invoice_number": payload.get("invoiceNumber"), "invoice_id": new_id,
            "grand_total": payload.get("grandTotal"),
        },
        run_id=run_id,
    )
    _store_local_invoice(
        db, event, payload, status="draft",
        crm_invoice_id=new_id,
    )
    return "created"


def _save_alerts(db: Session, event: Event, alerts: list[dict[str, str]]) -> None:
    db.query(Alert).filter(Alert.event_id == event.id).delete()
    for a in alerts:
        db.add(Alert(event_id=event.id, severity=a["severity"],
                     issue=a["issue"], action=a.get("action", "")))
    db.flush()


# Human-readable phase names for the CRM Activity error summary — the raw
# phase keys ("invoice", "square") don't tell a non-engineer what was being
# attempted when it failed.
_PHASE_LABELS = {
    "clean": "cleaning & filtering the event",
    "classify": "AI classification",
    "square": "Square reconciliation",
    "calculate": "invoice calculation",
    "invoice": "creating the invoice / syncing financials to KonaOS",
    "alerts": "checking alerts",
    "report": "updating the ledger",
}


def _error_summary(phase: str, exc: Exception) -> str:
    """A plain-language one-liner for the CRM Activity error row."""
    what = _PHASE_LABELS.get(phase, phase)
    text = str(exc)
    if "500" in text or "internalServerError" in text:
        reason = "KonaOS returned HTTP 500 (internal server error on their side)"
    elif "400" in text or "invalidJson" in text:
        reason = "KonaOS rejected the request (HTTP 400 — invalid data)"
    elif "401" in text or "403" in text:
        reason = "KonaOS authentication failed (session expired?)"
    elif "timeout" in text.lower() or "timed out" in text.lower():
        reason = "KonaOS did not respond in time (timeout)"
    else:
        reason = text[:200]
    return f"Failed while {what} — {reason}"


def _append_run_summary(db: Session, run: PipelineRun, note: Callable[[str], None]) -> None:
    """Append a plain-language per-event roll-up to the run log."""
    events = db.query(Event).filter(Event.run_id == run.id).all()
    errored = [e for e in events if e.status == "error"]
    skipped = [e for e in events if e.status == "skipped"]
    processed = [e for e in events if e.status in ("processed", "needs_review")]

    def _who(e: Event) -> str:
        return e.event_name or e.event_code or e.crm_event_id or "?"

    def _kind(e: Event) -> str:
        t = e.event_type or "?"
        m = e.billing_model or "?"
        return f"{t} / {m}"

    note("──────── RUN SUMMARY ────────")
    note(f"{len(processed)} processed · {len(skipped)} skipped · {len(errored)} errored")
    if errored:
        note(f"ERRORED ({len(errored)}):")
        for e in errored:
            note(f"  ✕ {_who(e)} [{_kind(e)}] — {e.error or 'error'}")
    if skipped:
        note(f"SKIPPED ({len(skipped)}):")
        for e in skipped:
            note(f"  – {_who(e)} — {e.status_reason or 'skipped'}")
    if processed:
        note(f"PROCESSED ({len(processed)}):")
        for e in processed:
            amt = e.final_invoice_amount or 0.0
            note(f"  ✓ {_who(e)} [{_kind(e)}] — invoice ${amt:,.2f}")
    note("─────────────────────────────")


def _mark_event_error(db: Session, run: PipelineRun, crm_id: str, error: str) -> Event:
    event = db.query(Event).filter(Event.crm_event_id == crm_id).one_or_none()
    if event is None:
        event = Event(crm_event_id=crm_id, run_id=run.id)
        db.add(event)
    event.status = "error"
    event.error = error
    db.commit()
    return event


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _r2(v: float) -> float:
    return round(v + 0.0, 2)


def _equip_suffix(update_event_result: Any) -> str:
    """Render the equipment/staff KonaosClient.update_event confirms are
    still assigned (see _equipment_names/_staff_names), so every run log
    line confirming a CRM write also confirms nothing on the event was
    silently emptied — by name, not just a count. Blank for a client (e.g.
    mocks/tests) that doesn't report these — never fabricate a value."""
    if not isinstance(update_event_result, dict):
        return ""
    equip = update_event_result.get("_equipment_names")
    staff = update_event_result.get("_staff_names")
    if equip is None and staff is None:
        return ""
    equip_str = ", ".join(equip) if equip else "none assigned"
    staff_str = ", ".join(staff) if staff else "none assigned"
    return f" · equipment: {equip_str} · staff: {staff_str}"


def _preserved_detail(update_event_result: Any) -> dict[str, Any]:
    """Structured counterpart to _equip_suffix, for CrmAuditEntry.detail
    (JSON) rather than a log-line string. Omits the keys entirely when the
    CRM client doesn't report them, so the UI can distinguish "confirmed
    preserved" from "unknown" instead of showing a fabricated empty state."""
    if not isinstance(update_event_result, dict):
        return {}
    out: dict[str, Any] = {}
    if update_event_result.get("_equipment_preserved") is not None:
        out["equipment_preserved"] = update_event_result["_equipment_preserved"]
        out["equipment_names"] = update_event_result.get("_equipment_names") or []
    if update_event_result.get("_staff_preserved") is not None:
        out["staff_preserved"] = update_event_result["_staff_preserved"]
        out["staff_names"] = update_event_result.get("_staff_names") or []
    return out


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


def _normalize_classification(cls: dict[str, Any]) -> dict[str, Any]:
    """Deterministic post-rules on top of the classifier's output.

    Selling events settle at the truck via Square — when the classifier fell
    back to the CHECK default because the notes had no payment language, the
    truthful default for a selling event is card. An explicit CASH (driver
    wrote it) is kept.
    """
    event_type = str(cls.get("EVENT_TYPE", "")).strip().lower()
    method = str(cls.get("PAYMENT_METHOD", "")).strip().upper()
    if event_type == "selling" and method in ("", "CHECK"):
        cls["PAYMENT_METHOD"] = "CREDIT_CARD"
    return cls


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
    # A pipeline run always owns the row — this reclaims any row a Google Sheet
    # import had created for the same event, and protects it from future imports.
    entry.source = "pipeline"
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
    # Check / Invoice = the invoiced total for host-billed events (invoice or
    # hybrid), regardless of how the client ultimately pays — confirmed against
    # the legacy sheet, which populates this column for CHECK, CASH, and
    # CREDIT_CARD invoice/hybrid rows alike. Was previously gated on
    # payment_method == "CHECK", which zeroed it out for e.g. a credit-card
    # invoice event even though the client was in fact billed that amount.
    event_type_lower = str(cls.get("EVENT_TYPE", "")).strip().lower()
    entry.check_invoice = invoice_total if event_type_lower in ("invoice", "hybrid") else 0.0
    entry.deposit = _num(cls.get("DEPOSIT_AMOUNT"))
    entry.taxable = taxable
    entry.giveback_amount = _num(calc.get("GIVEBACK_AMOUNT"))
    entry.location_fee = _num(cls.get("LOCATION_FEE"))
    # Derived sales columns — formulas match the legacy monthly sheet:
    #   Event Sales Collected (O) = net card + cash pre-tax
    #   Sales Tax Amount (P)      = card tax + cash tax
    #   Sales $ (Q)               = net card + card tax + tips + cash collected
    #   Net Event Sales (S)       = Event Sales Collected − giveback
    # Invoice events are host-billed with no at-truck collection: Event Sales
    # Collected AND Net Event Sales both equal the Check / Invoice amount (the
    # billed total). Other billed events with no at-event sale fall back to the
    # invoiced sale (subtotal) rather than sitting at 0.
    is_invoice_type = event_type_lower == "invoice"
    if is_invoice_type:
        billed = entry.check_invoice or invoice_total
        entry.event_sales_collected = billed
        # Sales Tax Amount = at-event card + cash tax only (0 for a pure invoice
        # event); the invoice's own tax is already inside `billed`, so it must
        # not be double-counted here.
        entry.sales_tax = _r2(entry.square_card_tax + entry.cash_tax)
        entry.sales_dollars = subtotal
        entry.net_event_sales = billed
    else:
        entry.event_sales_collected = _r2(entry.square_net_card + entry.cash_pre_tax)
        entry.sales_tax = _r2(entry.square_card_tax + entry.cash_tax)
        entry.sales_dollars = _r2(
            entry.square_net_card + entry.square_card_tax
            + entry.square_tips_card + cash_collected
        )
        # No at-event sale but there is an invoiced amount → use the invoiced sale.
        if entry.event_sales_collected == 0 and subtotal:
            entry.event_sales_collected = subtotal
        entry.net_event_sales = _r2(entry.event_sales_collected - entry.giveback_amount)
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
    # AI tracking — _usage is set by both classifiers; rule-based reports
    # model="rule-based" with zero tokens, so cost lands at $0.
    from app.config import settings as _cfg

    _usage = cls.get("_usage") or {}
    entry.ai_model = str(_usage.get("model", "") or "")
    entry.ai_prompt_tokens = int(_usage.get("prompt_tokens", 0) or 0)
    entry.ai_completion_tokens = int(_usage.get("completion_tokens", 0) or 0)
    entry.ai_cost_usd = round(
        entry.ai_prompt_tokens / 1e6 * _cfg.openai_input_cost_per_mtok
        + entry.ai_completion_tokens / 1e6 * _cfg.openai_output_cost_per_mtok,
        6,
    )
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
