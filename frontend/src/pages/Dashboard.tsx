import { useEffect, useState } from "react";
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
  const navigate = useNavigate();

  async function load(date = targetDate) {
    // Never blank `stats` on a refetch — doing so unmounts the whole page
    // (including the date picker being used) behind the full-page loader.
    setRefreshing(true);
    try {
      setStats(await api.stats(date ? { from_date: date, to_date: date } : {}));
    } finally {
      setRefreshing(false);
    }
  }
  useEffect(() => {
    load(targetDate);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetDate]);

  async function runPipeline() {
    setPhase("running");
    setResult(null);
    try {
      const res = await api.runPipeline(targetDate || undefined);
      // Poll while the run executes in the background — each poll refreshes
      // the live step list rendered in the modal.
      let run = await api.run(res.run_id);
      setResult(run);
      let guard = 0;
      while (run.status === "running" && guard < 600) {
        await new Promise((r) => setTimeout(r, 700));
        run = await api.run(res.run_id);
        setResult(run);
        guard++;
      }
      setResult(run);
      setPhase("done");
      await load();
    } catch {
      setPhase("idle");
    }
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
            disabled={phase === "running" || !targetDate}
            title={targetDate ? undefined : "Pick a date first — runs are date-scoped to keep them small and cheap."}
          >
            {phase === "running" ? (
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

      {targetDate && stats.total_events === 0 && (
        <div className="card" style={{ marginBottom: 16 }}>
          <strong>No events processed for {targetDate} yet.</strong>
          <div className="muted" style={{ marginTop: 4, fontSize: 13 }}>
            Hit <em>Run for {targetDate}</em> to process this day, or pick another date
            to see a day that's already been processed.
          </div>
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
              <span style={{ cursor: "pointer" }} onClick={() => navigate("/runs")}>
                #{stats.last_run.id} · {stats.last_run.status}
              </span>
            ) : (
              "—"
            )}
          </div>
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
          onViewRun={() => result && navigate("/runs")}
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
  onViewRun,
}: {
  phase: RunPhase;
  result: PipelineRun | null;
  targetDate: string;
  onClose: () => void;
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
