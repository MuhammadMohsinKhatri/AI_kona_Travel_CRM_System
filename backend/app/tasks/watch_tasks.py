"""Re-run events whose KonaOS source changed after we processed them.

The gap this closes: ThriftBooks (2026-07-29) was processed before its driver
typed "Served 31 Konas". Our copy said 0 servings, the invoice came to $0.00,
and the event sat on Needs Attention looking finished. Nothing was broken — the
snapshot was just older than the source. Notes get filled in after the fact
constantly, so this happens to any event that is processed once and never
looked at again.

This task re-fetches events we already hold, compares a fingerprint of the
billing-relevant fields (app/core/source_fingerprint.py), and re-runs the ones
that moved.

Two constraints shape the design, and both are the reason it isn't simply "poll
everything every five minutes":

  1. **KonaOS has no bulk change feed.** The events grid returns id / name /
     address / times / asset names and nothing else — no notes, no updatedAt
     (verified against the live API 2026-07-30). Detecting a note edit costs one
     details-minimal GET per event; there is no cheaper route.

  2. **The session dislikes being hammered.** Rapid bursts of requests have
     destabilised the KonaOS session key before, which breaks the whole
     integration and not just this task. So a pass checks at most
     ``DEFAULT_LIMIT`` events, paced ``PACE_SECONDS`` apart, oldest-checked
     first — a round-robin that covers the window over several passes instead of
     a stampede on every one.

The first pass over a pre-existing event records its fingerprint WITHOUT
re-running it. A change that predates the baseline is therefore missed, which is
the deliberate trade: treating "no fingerprint yet" as "changed" would re-run
every event in the window at once, and mass unattended writes to KonaOS is
exactly the 2026-07-21 incident.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

from app.core import event_cleaner
from app.core.source_fingerprint import changed_fields, fingerprint
from app.db.base import SessionLocal
from app.integrations import factory
from app.models import CrmAuditEntry, Event, PipelineRun
from app.tasks.celery_app import celery

# The change log's action key for an INBOUND edit. Every other action in that
# table is something we did TO KonaOS; this one is something KonaOS did to us,
# which is why it gets its own key rather than reusing event_updated.
AUDIT_ACTION = "source_changed"

# How far back to watch. Billing corrections land within a couple of weeks of
# the event; beyond that the books are closed and a re-run is a human decision.
DEFAULT_LOOKBACK_DAYS = 14

# Events checked per pass. 40 at PACE_SECONDS apart is ~1 minute of KonaOS
# traffic per hour — enough to cycle a two-week window several times a day.
DEFAULT_LIMIT = 40

# Gap between detail GETs. Deliberate: see constraint (2) above.
PACE_SECONDS = 1.5

# Statuses whose events are finished with. A cancelled event has nothing to
# re-bill, and "processing" means a run already has it.
SKIP_STATUSES = ("processing",)


def _is_cancelled(event: Event) -> bool:
    return "cancel" in str(event.final_status or "").lower()


def _log_source_change(db, event: Event, fresh: dict) -> list[dict[str, str]]:
    """Record the inbound edit on the KonaOS Change Log.

    Answers "how would I know this happened?" without having to notice a figure
    moved. The row names the fields that changed and carries before/after in its
    detail, so the log reads as a two-way history: what we wrote to KonaOS, and
    what KonaOS changed under us.

    Written from the watcher rather than the pipeline on purpose — by the time
    the re-run stores the event, the old snapshot it differed from is gone.
    """
    diffs = changed_fields(event.cleaned or {}, fresh)
    labels = sorted({d["label"] for d in diffs})
    summary = (
        f"Changed in Kona OS: {', '.join(labels)} — re-running this event"
        if labels else "Changed in Kona OS — re-running this event"
    )
    db.add(CrmAuditEntry(
        event_id=event.id, crm_event_id=event.crm_event_id,
        event_name=event.event_name, event_date=event.event_date,
        action=AUDIT_ACTION, summary=summary,
        detail={"fields_changed": labels, "changes": diffs},
    ))
    return diffs


@celery.task(name="app.tasks.watch_tasks.rerun_changed_events")
def rerun_changed_events(
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    limit: int = DEFAULT_LIMIT,
    pace_seconds: float = PACE_SECONDS,
) -> dict:
    """Check a batch of recent events against KonaOS; re-run the changed ones.

    Returns a summary dict (also useful from a shell for a one-off check).
    Every changed event goes into ONE pipeline run rather than one run each —
    the pipeline already accepts a list of ids, and a single run keeps the Runs
    page readable and can't race itself.
    """
    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
    db = SessionLocal()
    checked = baselined = changed = failed = 0
    changed_ids: list[str] = []

    try:
        candidates = (
            db.query(Event)
            .filter(
                Event.event_date >= cutoff,
                Event.crm_event_id != "",
                Event.status.notin_(SKIP_STATUSES),
            )
            # Oldest check first, never-checked before that, so the per-pass cap
            # rotates through the window instead of re-reading the same rows.
            .order_by(Event.source_checked_at.is_(None).desc(),
                      Event.source_checked_at.asc())
            .limit(limit)
            .all()
        )
        candidates = [e for e in candidates if not _is_cancelled(e)]

        crm = factory.get_crm()
        now = datetime.now(timezone.utc)

        for i, event in enumerate(candidates):
            if i and pace_seconds:
                time.sleep(pace_seconds)
            try:
                raw = crm.get_event(event.crm_event_id)
            except Exception:  # noqa: BLE001 — one bad fetch must not end the pass
                failed += 1
                continue
            if not raw:
                failed += 1
                continue

            cleaned = event_cleaner.clean_event(raw, brand_name=raw.get("brandName", ""))
            current = fingerprint(cleaned)
            checked += 1
            event.source_checked_at = now

            if not event.source_fingerprint:
                # Pre-existing row: record the baseline, don't re-run. See the
                # module docstring — this is the anti-stampede rule.
                event.source_fingerprint = current
                baselined += 1
            elif current != event.source_fingerprint:
                changed += 1
                changed_ids.append(event.crm_event_id)
                _log_source_change(db, event, cleaned)
                # The fingerprint is NOT written here. The pipeline writes it
                # when it stores the re-processed event, so a re-run that fails
                # leaves the event still marked as changed and gets picked up
                # again next pass.

        db.commit()

        run_id = None
        if changed_ids:
            run = PipelineRun(
                status="running", trigger="source_change",
                filter_event_ids=changed_ids,
            )
            db.add(run)
            db.commit()
            db.refresh(run)
            run_id = run.id
            from app.tasks.pipeline_tasks import run_pipeline_task
            run_pipeline_task.delay(run_id=run_id, trigger="source_change")

        return {
            "checked": checked,
            "baselined": baselined,
            "changed": changed,
            "failed": failed,
            "changed_ids": changed_ids,
            "run_id": run_id,
        }
    finally:
        db.close()
