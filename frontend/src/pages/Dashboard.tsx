import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, DashboardStats, PipelineRun } from "../api/client";
import { Loading, StepList, money } from "../components/ui";

type RunPhase = "idle" | "running" | "done";

/** Today in America/New_York — the business's day, not the browser's. */
function todayNY(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date());
}

export default function Dashboard() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  // One date drives both things: which day the tiles below describe, and which
  // day "Run" processes. Empty = all-time view (and running is disabled).
  const [targetDate, setTargetDate] = useState(todayNY());
  const [phase, setPhase] = useState<RunPhase>("idle");
  const [result, setResult] = useState<PipelineRun | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [dryRun, setDryRun] = useState<boolean | null>(null);
  // Set when the user sends a running pipeline to the background — the poll
  // loop must then stop touching state, or the "done" modal would pop back
  // up minutes later over whatever they're doing.
  const dismissed = useRef(false);
  const navigate = useNavigate();

  async function load(date = targetDate, silent = false) {
    // Never blank `stats` on a refetch — doing so unmounts the whole page
    // (including the date picker being used) behind the full-page loader.
    // `silent` skips the dimming too (used by the background-run poll).
    if (!silent) setRefreshing(true);
    try {
      setStats(await api.stats(date ? { from_date: date, to_date: date } : {}));
    } finally {
      if (!silent) setRefreshing(false);
    }
  }
  useEffect(() => {
    load(targetDate);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetDate]);

  // Invoice mode is server config — show it, don't make the user guess
  // whether drafts actually reach KonaOS.
  useEffect(() => {
    api.health().then((h) => setDryRun(h.pipeline_dry_run)).catch(() => {});
  }, []);

  // A run for this date is executing in the worker (manual, scheduled, or one
  // sent to the background) — keep the banner and tiles fresh until it ends.
  const runningForDate = stats?.date_run?.running ?? null;
  useEffect(() => {
    if (!runningForDate) return;
    const t = setInterval(() => load(targetDate, true), 5000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runningForDate?.id, targetDate]);

  const [runError, setRunError] = useState("");

  async function runPipeline() {
    dismissed.current = false;
    setRunError("");
    setPhase("running");
    setResult(null);
    try {
      const res = await api.runPipeline(targetDate || undefined);
      // Poll while the run executes in the background worker — each poll
      // refreshes the live step list rendered in the modal. The modal is only
      // a viewer: dismissing it leaves the run going on the server.
      let run = await api.run(res.run_id);
      setResult(run);
      let guard = 0;
      while (run.status === "running" && guard < 600) {
        if (dismissed.current) return;  // sent to background — stop watching
        await new Promise((r) => setTimeout(r, 700));
        run = await api.run(res.run_id);
        setResult(run);
        guard++;
      }
      if (dismissed.current) return;
      setResult(run);
      setPhase("done");
      await load();
    } catch (e: any) {
      if (!dismissed.current) {
        setPhase("idle");
        // e.g. 409 — another run is already processing this date.
        setRunError(e?.message || "Couldn't start the run.");
        await load(targetDate, true);
      }
    }
  }

  /** Close the modal while the run keeps executing on the server. */
  function sendToBackground() {
    dismissed.current = true;
    setPhase("idle");
  }

  if (!stats) return <Loading />;  // first load only

  return (
    <div style={{ opacity: refreshing ? 0.55 : 1, transition: "opacity .15s" }}>
      <div className="topbar">
        <div>
          <h1 className="page-title">Dashboard</h1>
          <p className="page-sub">
            Showing events dated <strong>{targetDate}</strong>
            {targetDate === todayNY() && " (today)"} — the figures below cover this day only.
          </p>
        </div>
        <div className="run-controls">
          <input
            className="date-input"
            type="date"
            value={targetDate}
            // The dashboard is always day-scoped: ignore attempts to clear the
            // date (native pickers offer a "clear" that would blank the view).
            onChange={(e) => e.target.value && setTargetDate(e.target.value)}
            title="Sets the day these figures cover, and the day Run processes."
          />
          {targetDate !== todayNY() && (
            <button className="btn" onClick={() => setTargetDate(todayNY())} title="Back to today">
              Today
            </button>
          )}
          <button
            className="btn primary"
            onClick={runPipeline}
            disabled={phase === "running" || !targetDate || !!runningForDate}
            title={
              runningForDate
                ? `Run #${runningForDate.id} is already processing this date`
                : targetDate ? undefined : "Pick a date first — runs are date-scoped to keep them small and cheap."
            }
          >
            {phase === "running" || runningForDate ? (
              <>
                <span className="spinner sm" /> &nbsp;Running…
              </>
            ) : targetDate ? (
              `▶ Run for ${targetDate}`
            ) : (
              "▶ Pick a date to run"
            )}
          </button>
        </div>
      </div>

      {/* Server invoice mode — make dry-run visible instead of a surprise. */}
      {dryRun === true && (
        <div className="card" style={{ marginBottom: 16, borderColor: "var(--warn)" }}>
          <strong>Invoice dry-run is ON.</strong>{" "}
          <span className="muted" style={{ fontSize: 13 }}>
            Runs calculate and store invoice drafts inside this app only — nothing is
            created in KonaOS. To create real KonaOS drafts, set{" "}
            <code>PIPELINE_DRY_RUN=false</code> in the server's backend .env and restart
            the backend container.
          </span>
        </div>
      )}

      {/* Run status for the selected date — always visible, so a background
          or scheduled run is obvious without opening any popup. */}
      {runError && (
        <div className="card" style={{ marginBottom: 16, borderColor: "var(--crit)" }}>
          <strong style={{ color: "var(--crit)" }}>Couldn't start the run:</strong> {runError}
        </div>
      )}
      {runningForDate ? (
        <div className="card" style={{ marginBottom: 16, borderColor: "var(--accent)" }}>
          <span className="spinner sm" />{" "}
          <strong>
            Run #{runningForDate.id} is processing {targetDate} right now
          </strong>
          <span className="muted">
            {" "}· {runningForDate.trigger} run
            {runningForDate.started_at &&
              `, started ${new Date(runningForDate.started_at).toLocaleTimeString()}`}
            {" "}— it executes on the server, you can keep working.
          </span>{" "}
          <button className="btn" style={{ marginLeft: 8 }} onClick={() => navigate("/runs")}>
            Watch live
          </button>
        </div>
      ) : stats.date_run?.last ? (
        <p className="muted" style={{ marginTop: -6, marginBottom: 16, fontSize: 13 }}>
          ✓ {targetDate} was last processed{" "}
          {stats.date_run.last.finished_at
            ? `at ${new Date(stats.date_run.last.finished_at).toLocaleString()}`
            : "earlier"}{" "}
          by run #{stats.date_run.last.id} ({stats.date_run.last.trigger},{" "}
          {stats.date_run.last.events_processed} events
          {stats.date_run.last.status === "failed" ? ", FAILED" : ""}). Re-running is safe —
          it refreshes the same rows with the latest CRM and Square data.
        </p>
      ) : null}

      {targetDate && stats.total_events === 0 && (
        <div className="card" style={{ marginBottom: 16 }}>
          <strong>No events processed for {targetDate} yet.</strong>
          <div className="muted" style={{ marginTop: 4, fontSize: 13 }}>
            Hit <em>Run for {targetDate}</em> to process this day, or pick another date
            to see a day that's already been processed.
          </div>
          {stats.last_run && !runningForDate && (
            <div className="muted" style={{ marginTop: 8, fontSize: 13 }}>
              Latest pipeline activity: run #{stats.last_run.id} ({stats.last_run.trigger}
              {stats.last_run.target_date ? ` · for ${stats.last_run.target_date}` : " · all dates"})
              {" "}{stats.last_run.status}
              {stats.last_run.finished_at &&
                ` at ${new Date(stats.last_run.finished_at).toLocaleString()}`}
              {" "}— {stats.last_run.events_processed} events, {stats.last_run.invoices_created} invoices.{" "}
              {stats.last_run.target_date && stats.last_run.target_date !== targetDate && (
                <span style={{ cursor: "pointer", textDecoration: "underline", marginRight: 8 }}
                  onClick={() => setTargetDate(stats.last_run!.target_date!)}>
                  See that day's numbers
                </span>
              )}
              <span style={{ cursor: "pointer", textDecoration: "underline" }}
                onClick={() => navigate("/runs")}>View runs</span>
            </div>
          )}
        </div>
      )}

      <div className="grid cols-4">
        <Stat label="Events" value={stats.total_events} />
        <Stat label="Invoices drafted" value={stats.total_invoices} />
        <Stat label="Invoiced amount" value={money(stats.invoiced_amount)} />
        <Stat
          label="Open alerts"
          value={stats.open_alerts}
          color={stats.open_alerts ? "var(--accent)" : undefined}
        />
      </div>

      <div className="grid cols-4" style={{ marginTop: 16 }}>
        <Stat small label="Needs review" value={stats.needs_review} />
        <Stat small label="Errored" value={stats.errored} />
        <div className="card stat" title="Total OpenAI classifier spend across all pipeline runs — per-run breakdown on the Pipeline Runs tab">
          <div className="label">AI cost (all runs)</div>
          <div className="value small" style={{ cursor: "pointer" }} onClick={() => navigate("/runs")}>
            ${stats.ai_usage.cost_usd.toFixed(2)}
            <span className="muted" style={{ fontSize: 12, fontWeight: 500 }}>
              {" "}· {(stats.ai_usage.total_tokens / 1000).toFixed(1)}k tok
            </span>
          </div>
        </div>
        <div className="card stat">
          <div className="label">Last run</div>
          <div className="value small">
            {stats.last_run ? (
              <span style={{ cursor: "pointer" }} onClick={() => navigate("/runs")}
                title="Open Pipeline Runs">
                #{stats.last_run.id} · {stats.last_run.status}
              </span>
            ) : (
              "—"
            )}
          </div>
          {stats.last_run && (
            <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
              {stats.last_run.trigger} · {stats.last_run.target_date || "all dates"}
              {stats.last_run.finished_at &&
                ` · ${new Date(stats.last_run.finished_at).toLocaleString(undefined,
                  { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}`}
            </div>
          )}
        </div>
      </div>

      <div className="grid cols-2" style={{ marginTop: 16 }}>
        <div className="card">
          <div className="section-title" style={{ marginTop: 0 }}>Events by event type</div>
          <BarList data={stats.events_by_event_type} capitalize />
        </div>
        <div className="card">
          <div className="section-title" style={{ marginTop: 0 }}>Open alerts by severity</div>
          <BarList data={stats.alerts_by_severity} emptyText="No open alerts 🎉" />
        </div>
      </div>

      {phase !== "idle" && (
        <RunModal
          phase={phase}
          result={result}
          targetDate={targetDate}
          onClose={() => setPhase("idle")}
          onBackground={sendToBackground}
          onViewRun={() => { dismissed.current = true; navigate("/runs"); }}
        />
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  small,
  color,
}: {
  label: string;
  value: string | number;
  small?: boolean;
  color?: string;
}) {
  return (
    <div className="card stat">
      <div className="label">{label}</div>
      <div className={"value" + (small ? " small" : "")} style={color ? { color } : undefined}>
        {value}
      </div>
    </div>
  );
}

function RunModal({
  phase,
  result,
  targetDate,
  onClose,
  onBackground,
  onViewRun,
}: {
  phase: RunPhase;
  result: PipelineRun | null;
  targetDate: string;
  onClose: () => void;
  onBackground: () => void;
  onViewRun: () => void;
}) {
  return (
    <div className="overlay">
      <div className="card modal">
        {phase === "running" ? (
          <>
            <div className="spinner" />
            <h2>Pipeline running…</h2>
            <p className="muted">
              {targetDate ? `Processing events for ${targetDate}` : "Processing all events"}
            </p>
            <StepList steps={result?.progress ?? []} />
            <div className="flex" style={{ justifyContent: "center", gap: 10, marginTop: 14 }}>
              <button className="btn" onClick={onViewRun}>Watch in Pipeline Runs</button>
              <button className="btn primary" onClick={onBackground}>Continue in background</button>
            </div>
            <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>
              The pipeline runs on the server — closing this window doesn't stop it.
              Follow it any time from the Pipeline Runs page.
            </p>
          </>
        ) : (
          <>
            <h2>
              {result?.status === "failed" ? "Run failed" : "Run complete"} · #{result?.id}
            </h2>
            <p className="muted">
              {result?.target_date ? `Date: ${result.target_date}` : "All events"}
            </p>
            <StepList steps={result?.progress ?? []} />
            {result?.status === "failed" ? (
              <p style={{ color: "var(--crit)" }}>{result?.error}</p>
            ) : (
              <>
                <div className="result-grid">
                  <Cell n={result?.events_processed ?? 0} l="Processed" />
                  <Cell n={result?.invoices_created ?? 0} l="Invoices" />
                  <Cell n={result?.alerts_raised ?? 0} l="Alerts" />
                  <Cell n={result?.events_fetched ?? 0} l="Fetched" />
                  <Cell n={result?.events_skipped ?? 0} l="Skipped" />
                  <Cell n={result?.events_errored ?? 0} l="Errored" />
                </div>
                {(result?.ai_cost_usd ?? 0) > 0 && (
                  <p className="muted" style={{ marginTop: 0 }}>
                    AI usage: {((result!.ai_prompt_tokens + result!.ai_completion_tokens) / 1000).toFixed(1)}k
                    tokens · ${result!.ai_cost_usd.toFixed(3)}
                  </p>
                )}
              </>
            )}
            <div className="flex" style={{ justifyContent: "center", gap: 10 }}>
              <button className="btn" onClick={onViewRun}>View run log</button>
              <button className="btn primary" onClick={onClose}>Done</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Cell({ n, l }: { n: number; l: string }) {
  return (
    <div className="cell">
      <div className="n">{n}</div>
      <div className="l">{l}</div>
    </div>
  );
}

function BarList({
  data,
  emptyText,
  capitalize,
}: {
  data: Record<string, number>;
  emptyText?: string;
  capitalize?: boolean;
}) {
  const entries = Object.entries(data);
  if (!entries.length) return <p className="muted">{emptyText ?? "No data yet."}</p>;
  const max = Math.max(...entries.map(([, v]) => v));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {entries.map(([k, v]) => (
        <div key={k}>
          <div className="flex between" style={{ fontSize: 13, marginBottom: 4 }}>
            <span style={capitalize ? { textTransform: "capitalize" } : undefined}>{k}</span>
            <strong>{v}</strong>
          </div>
          <div style={{ background: "var(--surface-2)", borderRadius: 6, height: 8 }}>
            <div
              style={{
                width: `${(v / max) * 100}%`,
                background: "var(--brand)",
                height: 8,
                borderRadius: 6,
              }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}
