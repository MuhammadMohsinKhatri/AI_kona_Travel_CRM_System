import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AlertDetail as AlertDetailData, api } from "../api/client";
import { Badge, Loading, money } from "../components/ui";

/** One alert, written to be actionable by someone who arrived from a Telegram
 *  notification on their phone and has no other context.
 *
 *  The shape is deliberately a checklist: what's wrong → which event → how to
 *  fix it → re-run. A non-technical user should be able to work top to bottom
 *  and finish with correct data, without asking anyone what a "billing model"
 *  is or which button re-runs the pipeline. */
export default function AlertDetailPage() {
  const { id } = useParams();
  const [data, setData] = useState<AlertDetailData | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [runMsg, setRunMsg] = useState("");
  const navigate = useNavigate();

  function load() {
    setError("");
    api.alert(Number(id))
      .then(setData)
      .catch((e: any) => setError(e?.message || "Couldn't load this alert"));
  }
  useEffect(load, [id]);

  async function rerun() {
    if (!data?.event) return;
    setBusy(true);
    setRunMsg("");
    try {
      const res = await api.runPipeline({ eventIds: [data.event.crm_event_id] });
      setRunMsg(
        `Re-running this event now (run #${res.run_id}). ` +
        "Watch it on the Automation Runs page — when it finishes, come back and " +
        "check whether the problem is gone."
      );
    } catch (e: any) {
      setRunMsg(`Couldn't start the re-run: ${e?.message || "unknown error"}`);
    } finally {
      setBusy(false);
    }
  }

  async function resolve() {
    setBusy(true);
    try {
      await api.resolveAlert(Number(id));
      load();
    } finally {
      setBusy(false);
    }
  }

  if (error) {
    return (
      <div className="card" style={{ borderColor: "var(--crit)" }}>
        <strong>Couldn't load this alert:</strong> {error}
        <div style={{ marginTop: 10 }}>
          <Link to="/alerts">← Back to Needs Attention</Link>
        </div>
      </div>
    );
  }
  if (!data) return <Loading />;

  const { alert, guide, event, can_rerun } = data;

  return (
    <>
      <p style={{ margin: "0 0 10px" }}>
        <Link to="/alerts">← Needs Attention</Link>
      </p>

      <div className="title-row" style={{ flexWrap: "wrap" }}>
        <h1 className="page-title">{alert.issue}</h1>
        <Badge kind={alert.severity}>{alert.severity}</Badge>
        {alert.resolved && <Badge kind="completed">Resolved</Badge>}
      </div>
      <p className="page-sub" style={{ marginBottom: 18 }}>
        {guide.label} · raised {new Date(alert.created_at).toLocaleString()}
      </p>

      {/* 1 — what's wrong, in plain words */}
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="section-title" style={{ marginTop: 0 }}>What's wrong</div>
        <p style={{ margin: 0, lineHeight: 1.65 }}>{guide.what}</p>
      </div>

      {/* 2 — which event, so it can be found in KonaOS */}
      {event ? (
        <div className="card" style={{ marginBottom: 14 }}>
          <div className="section-title" style={{ marginTop: 0 }}>Which event</div>
          <div className="kv">
            <div className="k">Event</div><div className="v">{event.event_name}</div>
            <div className="k">Date</div><div className="v">{event.event_date || "—"}</div>
            <div className="k">Brand</div><div className="v">{event.brand || "—"}</div>
            <div className="k">Event code</div><div className="v">{event.crm_event_id}</div>
            <div className="k">Type</div>
            <div className="v" style={{ textTransform: "capitalize" }}>
              {event.event_type || "—"}{event.billing_model ? ` · ${event.billing_model}` : ""}
            </div>
            <div className="k">Invoice amount</div>
            <div className="v">{money(event.final_invoice_amount)}</div>
          </div>
          <div style={{ marginTop: 12 }}>
            <Link className="btn" to={`/events/${event.id}`}
              state={{ from: `/alerts/${alert.id}`, label: "this alert" }}>
              Open the full event
            </Link>
          </div>
        </div>
      ) : (
        <div className="card" style={{ marginBottom: 14 }}>
          <div className="section-title" style={{ marginTop: 0 }}>Which event</div>
          <p className="muted" style={{ margin: 0 }}>
            This one isn't about a single event — it affects the whole system.
          </p>
        </div>
      )}

      {/* 3 — the specific fix from the rules engine */}
      <div className="card" style={{ marginBottom: 14, borderLeft: "4px solid var(--brand)" }}>
        <div className="section-title" style={{ marginTop: 0 }}>How to fix it</div>
        <p style={{ margin: "0 0 10px", lineHeight: 1.65, fontWeight: 600 }}>{alert.action}</p>
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>
          Fix this in: <strong>{guide.fix_in}</strong>
        </p>
      </div>

      {/* 4 — and what to do once it's fixed */}
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="section-title" style={{ marginTop: 0 }}>After you've fixed it</div>
        <p style={{ margin: "0 0 12px", lineHeight: 1.65 }}>{guide.after}</p>
        <div className="flex" style={{ gap: 10, flexWrap: "wrap" }}>
          {can_rerun && (
            <button className="btn primary" onClick={rerun} disabled={busy}>
              {busy ? "Starting…" : "▶ Re-run this event"}
            </button>
          )}
          {!alert.resolved && (
            <button className="btn" onClick={resolve} disabled={busy}
              title="Mark as sorted — it moves out of the open list but is kept as history">
              ✓ Mark as sorted
            </button>
          )}
          <button className="btn" onClick={() => navigate("/alerts")}>Back to the list</button>
        </div>
        {runMsg && (
          <div className="status ok" style={{ marginTop: 12 }}>{runMsg}</div>
        )}
      </div>

      {/* Only worth showing when a push was attempted and failed — otherwise
          it's noise about a channel the user may not even use. */}
      {alert.notify_error && alert.notify_error !== "Telegram not set up" && (
        <div className="card" style={{ borderColor: "var(--warn)" }}>
          <div className="section-title" style={{ marginTop: 0 }}>Telegram</div>
          <p className="muted" style={{ margin: 0, fontSize: 13 }}>
            This alert couldn't be sent to Telegram: {alert.notify_error}.{" "}
            <Link to="/settings">Check your settings</Link>.
          </p>
        </div>
      )}
    </>
  );
}
