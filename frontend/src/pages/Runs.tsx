import { useEffect, useState } from "react";
import { api, Page, PipelineRun } from "../api/client";
import { Badge, DeleteButton, Empty, Loading, StepList } from "../components/ui";

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
                  <td>{r.target_date || <span className="muted">all</span>}</td>
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
          <pre className="json" style={{ marginTop: 10 }}>
            {(selected.log || []).join("\n") || "(no log yet)"}
          </pre>
        </div>
      )}
    </>
  );
}
