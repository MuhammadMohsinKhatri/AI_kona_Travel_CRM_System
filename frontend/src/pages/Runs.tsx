import { useEffect, useState } from "react";
import { api, Page, PipelineRun } from "../api/client";
import { Badge, DeleteButton, Empty, Loading, RunEventBreakdown, StepList } from "../components/ui";

/** Run history — and the live view for runs executing in the worker.
 *
 *  Every run (manual or nightly) executes in the background Celery worker;
 *  the Dashboard popup is only a viewer. This page is where you re-attach:
 *  while any run is "running" the table auto-refreshes, and selecting a
 *  running run shows its live phase list + log. */
export default function Runs() {
  const [data, setData] = useState<Page<PipelineRun> | null>(null);
  const [selected, setSelected] = useState<PipelineRun | null>(null);

  const reload = () => api.runs().then(setData);

  useEffect(() => {
    reload();
  }, []);

  const anyRunning = (data?.items ?? []).some((r) => r.status === "running");

  // Keep the table fresh while a run is executing in the worker.
  useEffect(() => {
    if (!anyRunning) return;
    const t = setInterval(reload, 3000);
    return () => clearInterval(t);
  }, [anyRunning]);

  // The list payload is summary-only; the detail endpoint carries the live
  // progress steps. Poll it while the selected run is still executing.
  const selectedId = selected?.id ?? null;
  const selectedRunning = selected?.status === "running";
  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    const fetchDetail = () =>
      api.run(selectedId).then((r) => { if (!cancelled) setSelected(r); }).catch(() => {});
    fetchDetail();
    if (!selectedRunning) return () => { cancelled = true; };
    const t = setInterval(fetchDetail, 1500);
    return () => { cancelled = true; clearInterval(t); };
  }, [selectedId, selectedRunning]);

  return (
    <>
      <h1 className="page-title">Pipeline Runs</h1>
      <p className="page-sub">
        History of ingest → invoice executions. Runs execute in the background worker —
        closing the Dashboard popup (or refreshing) never stops one. Click a running run
        to watch its live progress; the nightly run fires at 11:30 PM New York time for
        that day's events.
      </p>

      {!data ? (
        <Loading />
      ) : data.items.length === 0 ? (
        <Empty text="No runs yet. Trigger one from the Dashboard." />
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>#</th><th>Trigger</th><th>Date filter</th><th>Status</th>
                <th className="right">Fetched</th><th className="right">Processed</th>
                <th className="right">Skipped</th><th className="right">Errored</th>
                <th className="right">Invoices</th><th className="right">Alerts</th>
                <th className="right">AI tokens</th><th className="right">AI cost</th>
                <th>Started</th>
                <th className="actions"></th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((r) => (
                <tr key={r.id} onClick={() => setSelected(r)}>
                  <td>{r.id}</td>
                  <td>{r.trigger}</td>
                  <td>
                    {r.filter_event_ids?.length ? (
                      <span title={r.filter_event_ids.join(", ")}>
                        {r.filter_event_ids.length} event{r.filter_event_ids.length > 1 ? "s" : ""}
                      </span>
                    ) : (
                      <>
                        {r.target_date || <span className="muted">all</span>}
                        {r.filter_event_types?.length ? (
                          <div className="muted" style={{ fontSize: 11.5 }}>
                            {r.filter_event_types.join(", ")}
                          </div>
                        ) : null}
                      </>
                    )}
                  </td>
                  <td><Badge kind={r.status}>{r.status}</Badge></td>
                  <td className="right">{r.events_fetched}</td>
                  <td className="right">{r.events_processed}</td>
                  <td className="right">{r.events_skipped}</td>
                  <td className="right">{r.events_errored}</td>
                  <td className="right">{r.invoices_created}</td>
                  <td className="right">{r.alerts_raised}</td>
                  <td className="right">
                    {r.ai_prompt_tokens + r.ai_completion_tokens > 0
                      ? ((r.ai_prompt_tokens + r.ai_completion_tokens) / 1000).toFixed(1) + "k"
                      : "—"}
                  </td>
                  <td className="right">
                    {r.ai_cost_usd > 0 ? "$" + r.ai_cost_usd.toFixed(3) : "—"}
                  </td>
                  <td>{new Date(r.started_at).toLocaleString()}</td>
                  <td className="actions">
                    <DeleteButton
                      title="Delete this run from history (events and ledger are unaffected)"
                      onDelete={async () => { await api.deleteRun(r.id); await reload(); }}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selected && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="flex between">
            <strong>
              Run #{selected.id}
              {selected.status === "running" && (
                <span className="muted" style={{ fontWeight: 400 }}> · live</span>
              )}
            </strong>
            <button className="btn" onClick={() => setSelected(null)}>Close</button>
          </div>
          <StepList steps={selected.progress ?? []} />
          {selected.error && (
            <p style={{ color: "var(--crit)" }}>{selected.error}</p>
          )}

          <RunEventBreakdown runId={selected.id} />

          <RunLog lines={selected.log ?? []} />
        </div>
      )}
    </>
  );
}

/** Readable, color-coded run log — the same lines as the raw text, but with
 *  the ISO timestamp shortened to a local time and each line tinted by what
 *  it means (red = error, amber = skipped/protected, green = accepted/created/
 *  synced) so the story of the run is scannable without parsing text. */
function RunLog({ lines }: { lines: string[] }) {
  if (!lines.length)
    return (
      <>
        <div className="section-title">Run log</div>
        <p className="muted">(no log yet)</p>
      </>
    );

  const tint = (msg: string): string | undefined => {
    if (/\bERROR\b|failed|error \d{3}/i.test(msg)) return "var(--crit)";
    if (/skipped|PROTECTED|filtered out/i.test(msg)) return "var(--warn)";
    if (/accepted|created|synced|updated|completed/i.test(msg)) return "var(--ok)";
    return undefined;
  };

  return (
    <>
      <div className="section-title">Run log</div>
      <div
        style={{
          background: "var(--surface-2)", border: "1px solid var(--border)",
          borderRadius: "var(--radius)", padding: "10px 12px", marginTop: 6,
          fontFamily: "var(--font-mono, monospace)", fontSize: 12.5, lineHeight: 1.7,
          maxHeight: 420, overflowY: "auto",
        }}
      >
        {lines.map((line, i) => {
          const sp = line.indexOf(" ");
          const ts = sp > 0 ? line.slice(0, sp) : "";
          const msg = sp > 0 ? line.slice(sp + 1) : line;
          const d = ts ? new Date(ts) : null;
          const time = d && !isNaN(d.getTime())
            ? d.toLocaleTimeString(undefined, { hour12: false })
            : "";
          return (
            <div key={i} style={{ display: "flex", gap: 10, color: tint(msg) }}>
              <span className="muted" style={{ flex: "none", opacity: 0.75 }}>{time}</span>
              <span style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{msg}</span>
            </div>
          );
        })}
      </div>
    </>
  );
}
