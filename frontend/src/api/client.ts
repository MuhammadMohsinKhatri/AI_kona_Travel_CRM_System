// Thin fetch wrapper that injects the JWT and normalizes errors.

const TOKEN_KEY = "konaice_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token);
}
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (!headers.has("Content-Type") && init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(path, { ...init, headers });
  if (res.status === 401) {
    clearToken();
    if (!path.includes("/auth/login")) window.location.href = "/login";
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  async login(email: string, password: string) {
    const form = new URLSearchParams();
    form.set("username", email);
    form.set("password", password);
    const data = await request<{ access_token: string }>("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: form.toString(),
    });
    setToken(data.access_token);
    return data;
  },
  me: () => request<User>("/api/auth/me"),
  stats: () => request<DashboardStats>("/api/dashboard/stats"),
  runPipeline: (targetDate?: string) =>
    request<RunTriggerResponse>("/api/pipeline/run", {
      method: "POST",
      body: JSON.stringify({ target_date: targetDate || null }),
    }),
  runs: () => request<Page<PipelineRun>>("/api/pipeline/runs"),
  run: (id: number) => request<PipelineRun>(`/api/pipeline/runs/${id}`),
  events: (params: Record<string, string> = {}) =>
    request<Page<EventSummary>>("/api/events?" + new URLSearchParams(params)),
  event: (id: number) => request<EventDetail>(`/api/events/${id}`),
  waiveCcFee: (id: number) =>
    request<EventDetail>(`/api/events/${id}/waive-cc-fee`, { method: "POST" }),
  invoices: (params: Record<string, string> = {}) =>
    request<Page<Invoice>>("/api/invoices?" + new URLSearchParams(params)),
  alerts: (params: Record<string, string> = {}) =>
    request<Page<Alert>>("/api/alerts?" + new URLSearchParams(params)),
  resolveAlert: (id: number) =>
    request<Alert>(`/api/alerts/${id}/resolve`, { method: "POST" }),
  konaosCreateEvent: (body: Record<string, unknown>) =>
    request<{ success?: boolean; message?: string; [k: string]: unknown }>(
      "/api/konaos/events",
      { method: "POST", body: JSON.stringify(body) }
    ),
  konaosSessionStatus: () =>
    request<KonaosSessionStatus>("/api/konaos/session/status"),
  konaosSessionUpdate: (sessionKey: string) =>
    request<{ updated: boolean; valid: boolean; detail: string }>(
      "/api/konaos/session",
      { method: "POST", body: JSON.stringify({ session_key: sessionKey }) }
    ),
};

export interface KonaosSessionStatus {
  configured: boolean;
  masked_key: string;
  obtained_days_ago: number | null;
  valid: boolean;
  hint: string | null;
}

// ── Types ────────────────────────────────────────────────────────────────
export interface User {
  id: number;
  email: string;
  full_name: string;
  is_admin: boolean;
}
export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}
export interface DashboardStats {
  total_events: number;
  needs_review: number;
  errored: number;
  total_invoices: number;
  invoiced_amount: number;
  open_alerts: number;
  alerts_by_severity: Record<string, number>;
  events_by_event_type: Record<string, number>;
  events_by_billing_model: Record<string, number>;
  ai_usage: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    cost_usd: number;
  };
  last_run: {
    id: number;
    status: string;
    started_at: string | null;
    events_processed: number;
    invoices_created: number;
    alerts_raised: number;
  } | null;
}
export interface RunTriggerResponse {
  run_id: number;
  mode: string;
  detail: string;
}
export interface PipelineStep {
  key: string;
  label: string;
  status: "pending" | "running" | "done" | "error";
  detail: string;
}
export interface PipelineRun {
  id: number;
  status: string;
  trigger: string;
  target_date: string | null;
  progress: PipelineStep[];
  events_fetched: number;
  events_processed: number;
  events_skipped: number;
  events_errored: number;
  invoices_created: number;
  alerts_raised: number;
  ai_prompt_tokens: number;
  ai_completion_tokens: number;
  ai_cost_usd: number;
  error: string | null;
  log: string[];
  started_at: string;
  finished_at: string | null;
}
export interface EventSummary {
  id: number;
  crm_event_id: string;
  event_code: string | null;
  event_name: string;
  brand: string;
  event_date: string | null;
  final_status: string;
  event_type: string;
  billing_model: string;
  final_invoice_amount: number;
  status: string;
  created_at: string;
  updated_at: string;
}
export interface Alert {
  id: number;
  severity: string;
  issue: string;
  action: string;
  resolved: boolean;
  created_at: string;
}
export interface Invoice {
  id: number;
  event_id: number;
  crm_invoice_id: string | null;
  invoice_number: string | null;
  title: string;
  invoice_type: string;
  status: string;
  grand_total: number;
  subtotal: number;
  tax_amount: number;
  due_amount: number;
  has_variance: boolean;
  variance_amount: number;
  payload: Record<string, unknown>;
  created_at: string;
}
export interface EventDetail extends EventSummary {
  error: string | null;
  raw: Record<string, unknown>;
  cleaned: Record<string, unknown>;
  classification: Record<string, unknown>;
  square: Record<string, unknown>;
  calculations: Record<string, unknown>;
  invoices: Invoice[];
  alerts: Alert[];
}
