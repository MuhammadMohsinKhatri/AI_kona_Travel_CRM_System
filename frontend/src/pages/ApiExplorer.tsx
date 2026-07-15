import { useEffect, useMemo, useState } from "react";
import { api, getToken, KonaosSessionStatus } from "../api/client";
import { Loading } from "../components/ui";

/** Renders the backend's live OpenAPI schema as a grouped, expandable
 *  endpoint reference with curl examples. Covers both the app's own API
 *  and the merged KonaOS CRM endpoints (/api/konaos/...). */

interface Param {
  name: string;
  in: string;
  required?: boolean;
  description?: string;
  schema?: { type?: string; default?: unknown };
}
interface Operation {
  method: string;
  path: string;
  summary: string;
  description: string;
  tags: string[];
  params: Param[];
  hasBody: boolean;
}

const TAG_INFO: Record<string, string> = {
  konaos:
    "The full Kona OS CRM API (events, staff, clients, invoices, reports) merged into this backend. " +
    "These endpoints proxy api.konaos.com with automatic session management. " +
    "Auth: your dashboard login token works, or send the X-API-Key header.",
  pipeline: "Trigger and monitor event → invoice pipeline runs.",
  events: "Events processed by the pipeline, with classification, calculations and Square data.",
  invoices: "Invoice drafts created by the pipeline (local mirror of what was sent to the CRM).",
  alerts: "Financial alerts raised by the pipeline that need human review.",
  dashboard: "Aggregated stats for the dashboard.",
  auth: "Login and session endpoints — POST /api/auth/login returns the bearer token.",
  health: "Service health and active provider configuration.",
};

const TAG_ORDER = ["konaos", "pipeline", "events", "invoices", "alerts", "dashboard", "auth", "health"];

export default function ApiExplorer() {
  const [spec, setSpec] = useState<any>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [activeTag, setActiveTag] = useState<string>("konaos");

  useEffect(() => {
    fetch("/openapi.json")
      .then((r) => r.json())
      .then(setSpec)
      .catch(() => setError("Could not load the API schema — is the backend running?"));
  }, []);

  const groups = useMemo(() => {
    if (!spec) return {};
    const out: Record<string, Operation[]> = {};
    for (const [path, methods] of Object.entries<any>(spec.paths ?? {})) {
      for (const [method, op] of Object.entries<any>(methods)) {
        if (!["get", "post", "put", "delete", "patch"].includes(method)) continue;
        const tag = (op.tags?.[0] ?? "other").toLowerCase();
        (out[tag] ??= []).push({
          method: method.toUpperCase(),
          path,
          summary: op.summary ?? "",
          description: op.description ?? "",
          tags: op.tags ?? [],
          params: (op.parameters ?? []) as Param[],
          hasBody: Boolean(op.requestBody),
        });
      }
    }
    return out;
  }, [spec]);

  const tags = useMemo(() => {
    const known = TAG_ORDER.filter((t) => groups[t]?.length);
    const rest = Object.keys(groups).filter((t) => !TAG_ORDER.includes(t)).sort();
    return [...known, ...rest];
  }, [groups]);

  if (error) return <p className="error-msg">{error}</p>;
  if (!spec) return <Loading />;

  const ops = (groups[activeTag] ?? []).filter(
    (o) =>
      !query ||
      o.path.toLowerCase().includes(query.toLowerCase()) ||
      o.summary.toLowerCase().includes(query.toLowerCase())
  );

  return (
    <>
      <h1 className="page-title">API Explorer</h1>
      <p className="page-sub">
        Every endpoint this backend exposes — including the merged Kona OS CRM APIs — generated
        live from the server's OpenAPI schema.
      </p>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="section-title" style={{ marginTop: 0 }}>How to call these APIs</div>
        <ol style={{ margin: "0 0 8px 18px", lineHeight: 1.9 }}>
          <li>
            Get a token: <code className="inline">POST /api/auth/login</code> (form fields{" "}
            <code className="inline">username</code>, <code className="inline">password</code>) → returns{" "}
            <code className="inline">access_token</code>.
          </li>
          <li>
            Send it on every request: <code className="inline">Authorization: Bearer &lt;token&gt;</code>.
          </li>
          <li>
            Kona OS endpoints (<code className="inline">/api/konaos/…</code>) also accept the legacy{" "}
            <code className="inline">X-API-Key</code> header, so existing GPT/n8n integrations keep working.
          </li>
        </ol>
        <p className="muted" style={{ margin: 0 }}>
          Interactive Swagger docs with request execution are at{" "}
          <a href="/docs" target="_blank" rel="noreferrer">/docs</a>.
        </p>
      </div>

      <KonaosSessionCard />

      <div className="toolbar">
        {tags.map((t) => (
          <button
            key={t}
            className={"btn" + (t === activeTag ? " primary" : "")}
            onClick={() => setActiveTag(t)}
          >
            {t} ({groups[t].length})
          </button>
        ))}
        <input
          className="input"
          style={{ maxWidth: 240, marginLeft: "auto" }}
          placeholder="Filter endpoints…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {TAG_INFO[activeTag] && (
        <p className="muted" style={{ marginTop: 0 }}>{TAG_INFO[activeTag]}</p>
      )}

      {ops.map((op) => (
        <EndpointRow key={op.method + op.path} op={op} />
      ))}
      {ops.length === 0 && <p className="muted">No endpoints match.</p>}
    </>
  );
}

function KonaosSessionCard() {
  const [status, setStatus] = useState<KonaosSessionStatus | null>(null);
  const [newKey, setNewKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  async function load() {
    try {
      setStatus(await api.konaosSessionStatus());
    } catch {
      setStatus(null);
    }
  }
  useEffect(() => {
    load();
  }, []);

  async function update() {
    if (!newKey.trim()) return;
    setBusy(true);
    setMsg("");
    try {
      const res = await api.konaosSessionUpdate(newKey.trim());
      setMsg(res.detail);
      setNewKey("");
      await load();
    } catch (e: any) {
      setMsg(e.message || "Update failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="flex between">
        <div className="section-title" style={{ margin: 0 }}>KonaOS Session</div>
        {status && (
          <span className={`badge ${status.valid ? "green" : "red"}`}>
            {status.valid ? "connected" : "session expired"}
          </span>
        )}
      </div>
      {!status ? (
        <p className="muted" style={{ marginBottom: 0 }}>Checking session…</p>
      ) : (
        <>
          <p className="muted" style={{ margin: "8px 0" }}>
            Key <code className="inline">{status.masked_key || "not set"}</code>
            {status.obtained_days_ago != null && <> · set {status.obtained_days_ago} days ago</>}
            {" "}· rotates ~every 15–30 days; a daily job auto-checks it and alerts when it dies.
          </p>
          {status.hint && <p className="error-msg">{status.hint}</p>}
          <div className="flex" style={{ gap: 10 }}>
            <input
              className="input"
              style={{ maxWidth: 380 }}
              placeholder="Paste new jsessionId from admin.konaos.com devtools…"
              value={newKey}
              onChange={(e) => setNewKey(e.target.value)}
            />
            <button className="btn primary" onClick={update} disabled={busy || !newKey.trim()}>
              {busy ? "Verifying…" : "Update key"}
            </button>
          </div>
          {msg && <p className="muted" style={{ marginBottom: 0, marginTop: 8 }}>{msg}</p>}
        </>
      )}
    </div>
  );
}

function EndpointRow({ op }: { op: Operation }) {
  const [open, setOpen] = useState(false);
  const queryParams = op.params.filter((p) => p.in === "query");
  const pathParams = op.params.filter((p) => p.in === "path");

  const curl = buildCurl(op);

  return (
    <div className="card endpoint" style={{ marginBottom: 10 }}>
      <div className="flex" style={{ cursor: "pointer", gap: 12 }} onClick={() => setOpen(!open)}>
        <span className={`method ${op.method}`}>{op.method}</span>
        <code className="path">{op.path}</code>
        <span className="muted" style={{ flex: 1, fontSize: 13 }}>{op.summary}</span>
        <span className="muted">{open ? "▲" : "▼"}</span>
      </div>

      {open && (
        <div style={{ marginTop: 14 }}>
          {op.description && (
            <p className="muted" style={{ whiteSpace: "pre-wrap", marginTop: 0 }}>
              {op.description.trim()}
            </p>
          )}

          {pathParams.length > 0 && <ParamTable title="Path parameters" params={pathParams} />}
          {queryParams.length > 0 && <ParamTable title="Query parameters" params={queryParams} />}
          {op.hasBody && (
            <p className="muted" style={{ fontSize: 13 }}>
              Requires a JSON request body — see <a href="/docs" target="_blank" rel="noreferrer">/docs</a> for the full schema.
            </p>
          )}

          <div className="section-title" style={{ margin: "12px 0 6px", fontSize: 13 }}>Example</div>
          <pre className="json">{curl}</pre>
        </div>
      )}
    </div>
  );
}

function ParamTable({ title, params }: { title: string; params: Param[] }) {
  return (
    <>
      <div className="section-title" style={{ margin: "12px 0 6px", fontSize: 13 }}>{title}</div>
      <table style={{ marginBottom: 8 }}>
        <thead>
          <tr><th>Name</th><th>Type</th><th>Required</th><th>Description</th></tr>
        </thead>
        <tbody>
          {params.map((p) => (
            <tr key={p.name} style={{ cursor: "default" }}>
              <td><code className="inline">{p.name}</code></td>
              <td>{p.schema?.type ?? "—"}</td>
              <td>{p.required ? "yes" : "no"}</td>
              <td className="muted" style={{ whiteSpace: "normal" }}>{p.description ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function buildCurl(op: Operation): string {
  const base = window.location.origin;
  const q = op.params
    .filter((p) => p.in === "query" && p.required)
    .map((p) => `${p.name}=<${p.schema?.type ?? "value"}>`)
    .join("&");
  const path = op.path.replace(/\{(\w+)\}/g, "<$1>");
  const token = getToken() ? "$TOKEN" : "<your-token>";
  let cmd = `curl -X ${op.method} '${base}${path}${q ? "?" + q : ""}' \\\n  -H 'Authorization: Bearer ${token}'`;
  if (op.hasBody) cmd += ` \\\n  -H 'Content-Type: application/json' \\\n  -d '{...}'`;
  return cmd;
}
