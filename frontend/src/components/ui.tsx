import { ReactNode } from "react";

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
