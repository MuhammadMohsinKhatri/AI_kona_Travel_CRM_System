import { ReactNode, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, EventSummary, PipelineStep } from "../api/client";

export function Badge({ kind, children }: { kind: string; children: ReactNode }) {
  const map: Record<string, string> = {
    // statuses
    processed: "green",
    needs_review: "amber",
    error: "red",
    skipped: "gray",
    pending: "gray",
    processing: "blue",
    draft: "blue",
    dry_run: "amber",
    completed: "green",
    running: "blue",
    failed: "red",
    // severities
    CRITICAL: "red",
    HIGH: "red",
    MEDIUM: "amber",
    LOW: "blue",
    // CRM audit actions
    invoice_created: "green",
    invoice_deleted: "red",
    invoice_skipped: "amber",
    event_updated: "blue",
  };
  const cls = map[children as string] ?? map[kind] ?? "gray";
  return <span className={`badge ${cls}`}>{children}</span>;
}

export function money(v: number | null | undefined): string {
  if (v == null) return "—";
  return "$" + v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function Loading() {
  return <div className="loading">Loading…</div>;
}

const AUDIT_VALUE_LABELS: Record<string, string> = {
  ccAmount: "Card",
  taxPercent: "Tax rate",
  tipAmount: "Tips",
  giveback: "Giveback",
  givebackPercentage: "Giveback %",
  invoiceAmount: "Invoice amount",
  invoiceStatus: "Invoice status",
};

function formatAuditValue(field: string, value: unknown): string {
  if (typeof value === "number") {
    // taxPercent is now sent as a percentage (6 = 6%). Older audit rows stored
    // it as a fraction (0.06); treat a sub-1 value as a fraction so both render
    // "6%" correctly.
    if (field === "taxPercent") {
      const pct = value < 1 ? value * 100 : value;
      return pct.toFixed(0) + "%";
    }
    if (field === "givebackPercentage") return value.toFixed(1) + "%";
    return money(value);
  }
  return String(value);
}

/** Renders a CrmAuditEntry's structured `detail` — the financial values that
 *  were actually synced to KonaOS (bolded), and confirmation of the
 *  equipment/staff that were already assigned and left untouched (or a
 *  clear warning if the event currently has none). Shared by the CRM
 *  Activity page and the per-event "KonaOS activity" section on EventDetail
 *  so both read this exactly the same way. Renders nothing for entries with
 *  no structured detail (e.g. invoice created/deleted/skipped). */
export function AuditDetail({ detail }: { detail: Record<string, unknown> | null | undefined }) {
  if (!detail) return null;
  const values = (detail.values as Record<string, unknown>) || {};
  const valueEntries = Object.entries(values);
  const hasEquip = "equipment_names" in detail;
  const hasStaff = "staff_names" in detail;
  const equipNames = (detail.equipment_names as string[]) || [];
  const staffNames = (detail.staff_names as string[]) || [];

  if (!valueEntries.length && !hasEquip && !hasStaff) return null;

  return (
    <div style={{ marginTop: 4, fontSize: 12, lineHeight: 1.6 }}>
      {valueEntries.length > 0 && (
        <div>
          {valueEntries.map(([k, v]) => (
            <span key={k} style={{ marginRight: 12 }}>
              {AUDIT_VALUE_LABELS[k] || k}: <strong>{formatAuditValue(k, v)}</strong>
            </span>
          ))}
        </div>
      )}
      {(hasEquip || hasStaff) && (
        <div className="muted">
          {hasEquip && (
            equipNames.length > 0
              ? <>Equipment: <strong>{equipNames.join(", ")}</strong></>
              : <span style={{ color: "var(--warn)" }}>⚠ No equipment currently assigned</span>
          )}
          {hasEquip && hasStaff && " · "}
          {hasStaff && (
            staffNames.length > 0
              ? <>Staff: <strong>{staffNames.join(", ")}</strong></>
              : <span style={{ color: "var(--warn)" }}>⚠ No staff currently assigned</span>
          )}
        </div>
      )}
    </div>
  );
}

/** Live phase list for a pipeline run — shared by the Dashboard's run modal
 *  and the Runs page, so a run's progress can be re-attached to from /runs
 *  after the modal is closed or the page refreshed. */
export function StepList({ steps }: { steps: PipelineStep[] }) {
  if (!steps.length) {
    return <p className="muted">Starting…</p>;
  }
  return (
    <div className="step-list">
      {steps.map((s) => (
        <div key={s.key} className={`step-row ${s.status}`}>
          <span className="icon">
            {s.status === "done" ? (
              "✓"
            ) : s.status === "error" ? (
              "✕"
            ) : s.status === "running" ? (
              <span className="spinner sm" />
            ) : (
              "○"
            )}
          </span>
          <span className="lbl">{s.label}</span>
          <span className="dtl">{s.detail}</span>
        </div>
      ))}
    </div>
  );
}

export function Empty({ text }: { text: string }) {
  return <div className="loading">{text}</div>;
}

/** Plain-language summary of what happened to every event a run touched —
 *  grouped into Errored / Skipped / Processed so anyone can read, at a glance,
 *  which events failed and why, which were skipped and why, and which went
 *  through (with their event type + billing model). Shared by the Pipeline
 *  Runs page and the Dashboard's last-run panel so both read identically.
 *  `from`/`fromLabel` set where the "← back" breadcrumb returns to. */
export function RunEventBreakdown({
  runId,
  from = "/runs",
  fromLabel = "Pipeline Runs",
  compact = false,
}: {
  runId: number;
  from?: string;
  fromLabel?: string;
  compact?: boolean;
}) {
  const [events, setEvents] = useState<EventSummary[] | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    setEvents(null);
    api
      .events({ run_id: String(runId), page_size: "200" })
      .then((p) => setEvents(p.items))
      .catch(() => setEvents([]));
  }, [runId]);

  if (events === null) return <div style={{ marginTop: 12 }}><Loading /></div>;
  if (events.length === 0)
    return (
      <p className="muted" style={{ marginTop: 12, fontSize: 13 }}>
        No events are attributed to this run — either nothing was fetched, or a later
        run re-processed the same events and now owns them. Re-run this date to re-link them.
      </p>
    );

  const errored = events.filter((e) => e.status === "error");
  const skipped = events.filter((e) => e.status === "skipped");
  const processed = events.filter((e) => e.status === "processed" || e.status === "needs_review");

  const open = (e: EventSummary) =>
    navigate(`/events/${e.id}`, { state: { from, label: fromLabel } });

  const typeTag = (e: EventSummary) =>
    e.event_type ? (
      <span className="badge blue" style={{ textTransform: "capitalize" }}>{e.event_type}</span>
    ) : null;

  return (
    <div style={{ marginTop: compact ? 4 : 16 }}>
      {!compact && (
        <div className="section-title" style={{ marginTop: 0 }}>
          Summary — what happened to each event ({events.length})
        </div>
      )}

      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, margin: "4px 0 14px", fontSize: 14 }}>
        <span><strong style={{ color: "var(--ok)" }}>{processed.length}</strong> processed</span>
        <span><strong style={{ color: "var(--warn)" }}>{skipped.length}</strong> skipped</span>
        <span><strong style={{ color: "var(--crit)" }}>{errored.length}</strong> errored</span>
      </div>

      {errored.length > 0 && (
        <GroupSection color="var(--crit)" title={`Errored (${errored.length})`}
          hint="Something failed for these events — the reason is shown in red.">
          {errored.map((e) => (
            <div key={e.id} className="run-ev" onClick={() => open(e)}>
              <div className="run-ev-head">
                <span style={{ fontWeight: 600 }}>{e.event_name || "(unnamed)"}</span>
                <span className="muted" style={{ fontSize: 12 }}>{e.event_code || e.crm_event_id}</span>
                {typeTag(e)}
                {e.billing_model && <span className="muted" style={{ fontSize: 12 }}>{e.billing_model}</span>}
              </div>
              <div style={{ color: "var(--crit)", fontSize: 13, marginTop: 3 }}>
                {e.error || "Errored (no detail recorded)."}
              </div>
            </div>
          ))}
        </GroupSection>
      )}

      {skipped.length > 0 && (
        <GroupSection color="var(--warn)" title={`Skipped (${skipped.length})`}
          hint="Filtered out before processing — never invoiced or synced.">
          {skipped.map((e) => (
            <div key={e.id} className="run-ev" onClick={() => open(e)}>
              <div className="run-ev-head">
                <span style={{ fontWeight: 600 }}>{e.event_name || "(unnamed)"}</span>
                <span className="muted" style={{ fontSize: 12 }}>{e.event_code || e.crm_event_id}</span>
              </div>
              <div className="muted" style={{ fontSize: 13, marginTop: 3 }}>
                Reason: {e.status_reason || "—"}
              </div>
            </div>
          ))}
        </GroupSection>
      )}

      {processed.length > 0 && (
        <GroupSection color="var(--ok)" title={`Processed (${processed.length})`}
          hint="Went all the way through. Event type + billing model shown.">
          {processed.map((e) => (
            <div key={e.id} className="run-ev" onClick={() => open(e)}>
              <div className="run-ev-head">
                <span style={{ fontWeight: 600 }}>{e.event_name || "(unnamed)"}</span>
                <span className="muted" style={{ fontSize: 12 }}>{e.event_code || e.crm_event_id}</span>
                {typeTag(e)}
                {e.billing_model && <span className="muted" style={{ fontSize: 12 }}>{e.billing_model}</span>}
                {e.status === "needs_review" && <Badge kind="needs_review">needs review</Badge>}
                <span style={{ marginLeft: "auto", fontWeight: 600 }}>{money(e.final_invoice_amount)}</span>
              </div>
            </div>
          ))}
        </GroupSection>
      )}
    </div>
  );
}

/** A titled group of event rows with a colored left accent. */
function GroupSection({
  color, title, hint, children,
}: {
  color: string; title: string; hint: string; children: ReactNode;
}) {
  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 6, flexWrap: "wrap" }}>
        <strong style={{ color }}>{title}</strong>
        <span className="muted" style={{ fontSize: 12 }}>{hint}</span>
      </div>
      <div style={{ borderLeft: `3px solid ${color}`, background: "var(--surface-2)", padding: "2px 0" }}>
        {children}
      </div>
    </div>
  );
}

/** Per-row delete. Two-step: the first click arms it, the second confirms —
 *  a plain window.confirm is easy to click through, and rows sit close
 *  together in dense tables. Auto-disarms after 4s.
 *
 *  `stopPropagation` matters: most rows are click-to-navigate. */
export function DeleteButton({
  onDelete,
  title = "Delete",
  confirmTitle = "Click again to confirm — this cannot be undone",
}: {
  onDelete: () => Promise<void> | void;
  title?: string;
  confirmTitle?: string;
}) {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), 4000);
    return () => clearTimeout(t);
  }, [armed]);

  return (
    <button
      className={"btn icon-btn" + (armed ? " danger" : "")}
      disabled={busy}
      title={armed ? confirmTitle : title}
      onClick={async (e) => {
        e.stopPropagation();
        if (!armed) {
          setArmed(true);
          return;
        }
        setBusy(true);
        try {
          await onDelete();
        } finally {
          setBusy(false);
          setArmed(false);
        }
      }}
    >
      {busy ? "…" : armed ? "Confirm?" : "🗑"}
    </button>
  );
}

/** Bulk delete for "everything matching the current filters". Same two-step
 *  arm/confirm as DeleteButton, but labeled with the row count so the user
 *  sees exactly how many rows are about to go. */
export function BulkDeleteButton({
  count,
  onDelete,
  noun = "events",
}: {
  count: number;
  onDelete: () => Promise<void> | void;
  noun?: string;
}) {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), 5000);
    return () => clearTimeout(t);
  }, [armed]);

  return (
    <button
      className={"btn" + (armed ? " danger" : "")}
      disabled={busy}
      style={{ marginLeft: "auto" }}
      title={
        armed
          ? "Click again to confirm — this cannot be undone"
          : `Delete every ${noun.replace(/s$/, "")} matching the current filters`
      }
      onClick={async () => {
        if (!armed) {
          setArmed(true);
          return;
        }
        setBusy(true);
        try {
          await onDelete();
        } finally {
          setBusy(false);
          setArmed(false);
        }
      }}
    >
      {busy
        ? "Deleting…"
        : armed
        ? `Really delete ${count} ${noun}?`
        : `🗑 Delete ${count} filtered`}
    </button>
  );
}
