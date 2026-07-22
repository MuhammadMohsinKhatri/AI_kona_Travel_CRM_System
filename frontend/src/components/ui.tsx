import { ReactNode, useEffect, useState } from "react";
import { PipelineStep } from "../api/client";

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
    if (field === "taxPercent") return (value * 100).toFixed(0) + "%";
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
