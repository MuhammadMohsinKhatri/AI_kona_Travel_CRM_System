import { ReactNode, useEffect, useState } from "react";

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
