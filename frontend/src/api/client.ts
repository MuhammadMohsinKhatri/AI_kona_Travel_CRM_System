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
  health: () =>
    request<{
      status: string;
      environment: string;
      pipeline_dry_run: boolean;
      providers: Record<string, string>;
    }>("/health"),
  stats: (params: Record<string, string> = {}) =>
    request<DashboardStats>("/api/dashboard/stats?" + new URLSearchParams(params)),
  runPipeline: (
    opts: { targetDate?: string; eventTypes?: string[]; eventIds?: string[] } = {}
  ) =>
    request<RunTriggerResponse>("/api/pipeline/run", {
      method: "POST",
      body: JSON.stringify({
        target_date: opts.targetDate || null,
        event_types: opts.eventTypes?.length ? opts.eventTypes : null,
        event_ids: opts.eventIds?.length ? opts.eventIds : null,
      }),
    }),
  runs: () => request<Page<PipelineRun>>("/api/pipeline/runs"),
  run: (id: number) => request<PipelineRun>(`/api/pipeline/runs/${id}`),
  deleteRun: (id: number) =>
    request<void>(`/api/pipeline/runs/${id}`, { method: "DELETE" }),
  events: (params: Record<string, string> = {}) =>
    request<Page<EventSummary>>("/api/events?" + new URLSearchParams(params)),
  event: (id: number) => request<EventDetail>(`/api/events/${id}`),
  deleteEvent: (id: number) =>
    request<void>(`/api/events/${id}`, { method: "DELETE" }),
  deleteEvents: (params: Record<string, string>) =>
    request<{ deleted: number }>("/api/events?" + new URLSearchParams(params), {
      method: "DELETE",
    }),
  waiveCcFee: (id: number) =>
    request<EventDetail>(`/api/events/${id}/waive-cc-fee`, { method: "POST" }),
  invoices: (params: Record<string, string> = {}) =>
    request<Page<Invoice>>("/api/invoices?" + new URLSearchParams(params)),
  invoiceMonths: () => request<string[]>("/api/invoices/months"),
  deleteInvoice: (id: number) =>
    request<void>(`/api/invoices/${id}`, { method: "DELETE" }),
  alerts: (params: Record<string, string> = {}) =>
    request<Page<Alert>>("/api/alerts?" + new URLSearchParams(params)),
  resolveAlert: (id: number) =>
    request<Alert>(`/api/alerts/${id}/resolve`, { method: "POST" }),
  deleteAlert: (id: number) =>
    request<void>(`/api/alerts/${id}`, { method: "DELETE" }),
  crmAudit: (params: Record<string, string> = {}) =>
    request<CrmAuditResponse>("/api/crm-audit?" + new URLSearchParams(params)),
  financialMonths: () => request<string[]>("/api/financials/months"),
  financials: (params: Record<string, string> = {}) =>
    request<FinancialsResponse>("/api/financials?" + new URLSearchParams(params)),
  deleteFinancialEntry: (id: number) =>
    request<void>(`/api/financials/${id}`, { method: "DELETE" }),
  deleteFinancials: (params: Record<string, string>) =>
    request<{ deleted: number }>("/api/financials?" + new URLSearchParams(params), {
      method: "DELETE",
    }),
  /** Record the cash counted for an event. Keyed by KonaOS event id — the
   *  same endpoint the cash automation posts to, so the UI and the bot go
   *  through identical logic. `source: "manual"` marks it as typed by a
   *  person rather than posted by a machine. */
  setEventCash: (crmEventId: string, cash: number, by = "") =>
    request<CashUpdateResult>(
      `/api/financials/by-event/${encodeURIComponent(crmEventId)}/cash`,
      {
        method: "PATCH",
        body: JSON.stringify({ cash_collected: cash, source: "manual", by }),
      }
    ),
  /** Set deposit / taxable / paid / payment method. Recorded and shown, but
   *  deliberately inert: nothing else recalculates from these yet. */
  setEventFields: (
    crmEventId: string,
    fields: Partial<Pick<FinancialRow, "deposit" | "taxable" | "paid" | "payment_method">>,
    by = ""
  ) =>
    request<FieldsUpdateResult>(
      `/api/financials/by-event/${encodeURIComponent(crmEventId)}/fields`,
      { method: "PATCH", body: JSON.stringify({ ...fields, source: "manual", by }) }
    ),
  clearEventCash: (crmEventId: string) =>
    request<CashUpdateResult>(
      `/api/financials/by-event/${encodeURIComponent(crmEventId)}/cash`,
      { method: "DELETE" }
    ),
  importFinancialsSheet: (sheet: "kona" | "tom" = "kona") =>
    request<SheetImportResult>(
      "/api/financials/import-sheet?" + new URLSearchParams({ sheet }),
      { method: "POST" }
    ),
  alert: (id: number) => request<AlertDetail>(`/api/alerts/${id}`),
  telegramSettings: () => request<TelegramSettings>("/api/settings/telegram"),
  saveTelegramSettings: (body: TelegramSettingsInput) =>
    request<TelegramSettings>("/api/settings/telegram", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  testTelegram: () =>
    request<TelegramTestResult>("/api/settings/telegram/test", { method: "POST" }),
  konaosFormOptions: () => request<FormOptions>("/api/konaos/form-options"),
  konaosQuickCreate: (body: Record<string, unknown>) =>
    request<QuickCreateResult>("/api/konaos/events/quick-create", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  konaosSessionStatus: () =>
    request<KonaosSessionStatus>("/api/konaos/session/status"),
  konaosSessionUpdate: (sessionKey: string) =>
    request<{ updated: boolean; valid: boolean; detail: string }>(
      "/api/konaos/session",
      { method: "POST", body: JSON.stringify({ session_key: sessionKey }) }
    ),
};

export interface FormOptions {
  brands: { id: string; label: string; frontendBaseUrl: string }[];
  statuses: { value: string; label: string }[];
  frequencies: { value: string; label: string }[];
  industries: { id: string; type: string }[];
  adminBaseUrl: string;
}
export interface QuickCreateResult {
  success: boolean;
  message: string;
  eventId: string | null;
  editUrl: string | null;
  driverNotesWritten?: boolean;
  driverNotesError?: string | null;
}

export interface CashUpdateResult {
  event_id: number;
  crm_event_id: string;
  cash_collected: number;
  source: "api" | "manual" | "ai";
  recomputed: Record<string, number>;
  min_guarantee: boolean;
  minimum_required: number;
  shortfall: number;
  awaiting_cash: boolean;
  invoice_needed: boolean;
  /** Set when posting cash unblocked a min-guarantee event: the id of the
   *  single-event run that remakes its invoice decision. Follow it on the
   *  Automation Runs page. */
  settlement_run_id?: number | null;
}

/** Where a field's current value came from. "ai" = the classifier read it out
 *  of the driver's notes (treat as a guess); "api" = an automation posted it;
 *  "manual" = a person typed it. */
export type FieldSource = "api" | "manual" | "ai";

export interface FieldsUpdateResult {
  crm_event_id: string;
  updated: Record<string, unknown>;
  sources: Record<string, FieldSource>;
  /** Always false for now — these fields are stored, not acted on. */
  recalculated: boolean;
}

export interface FinancialRow {
  id: number;
  event_id: number;
  crm_event_id: string;
  /** Per-field provenance, keyed by field name (cash_collected, deposit, …). */
  sources?: Record<string, FieldSource>;
  awaiting_cash?: boolean;
  minimum_required?: number;
  event_date: string | null;
  event_name: string;
  event_code: string | null;
  brand: string;
  final_status: string;
  event_type: string;
  billing_model: string;
  units_served: number;
  subtotal: number;
  sales_tax: number;
  cc_fee: number;
  check_invoice: number;
  // Square breakdown
  square_gross_sales: number;
  square_discounts: number;
  square_net_card: number;
  square_card_tax: number;
  square_tips_card: number;
  square_cc_fee: number;
  square_orders: number;
  square_device: string | null;
  // Cash split
  cash_collected: number;
  cash_tax: number;
  cash_pre_tax: number;
  // Billing
  taxable: boolean;
  event_sales_collected: number;
  sales_dollars: number;
  giveback_amount: number;
  net_event_sales: number;
  location_fee: number;
  invoice_total: number;
  deposit: number;
  balance_due: number;
  payment_method: string;
  paid: boolean;
  has_variance: boolean;
  variance_amount: number;
  // Reasoning + AI tracking
  note: string;
  ai_model: string;
  ai_prompt_tokens: number;
  ai_completion_tokens: number;
  ai_cost_usd: number;
  updated_at: string | null;
}
export interface CrmAuditEntry {
  id: number;
  event_id: number | null;
  crm_event_id: string;
  event_name: string;
  event_date: string | null;
  run_id: number | null;
  // event_updated | invoice_created | invoice_deleted | invoice_skipped
  action: string;
  summary: string;
  detail: Record<string, unknown>;
  created_at: string | null;
}
export interface CrmAuditResponse {
  items: CrmAuditEntry[];
  total: number;
  page: number;
  page_size: number;
  actions: string[];
}
export interface SheetImportResult {
  sheet: string;
  label: string;
  brand: string;
  created: number;
  updated: number;
  skipped_protected: number;
  placeholders_created: number;
  skipped_blank: number;
  source_url: string;
}
export interface FinancialsResponse {
  items: FinancialRow[];
  total: number;
  brands: string[];
  event_types: string[];
  totals: {
    subtotal: number;
    sales_tax: number;
    cc_fee: number;
    invoice_total: number;
    balance_due: number;
    square_sales: number;
    check_invoice: number;
    units_served: number;
  };
}

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
export interface DateRunInfo {
  id: number;
  status: string;
  trigger: string;
  started_at: string | null;
  finished_at: string | null;
  events_fetched: number;
  events_processed: number;
  events_skipped: number;
  events_errored: number;
  invoices_created: number;
  alerts_raised: number;
  error: string | null;
}
export interface DashboardStats {
  scope: { from_date: string | null; to_date: string | null; all_time: boolean };
  /** Single-day view only: the run currently processing this date (if any)
   *  and the most recent finished run for it. Null when not day-scoped. */
  date_run: { running: DateRunInfo | null; last: DateRunInfo | null } | null;
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
    trigger: string;
    target_date: string | null;
    started_at: string | null;
    finished_at: string | null;
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
  filter_event_types?: string[] | null;
  filter_event_ids?: string[] | null;
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
  status_reason: string;
  error: string | null;
  created_at: string;
  updated_at: string;
}
/** Which system raised the alert — decides the guidance shown for fixing it. */
export type AlertSource = "financial" | "cash" | "session";

export interface Alert {
  id: number;
  severity: string;
  issue: string;
  action: string;
  resolved: boolean;
  created_at: string;
  /** Event this alert is about. Null for system alerts (e.g. session key). */
  event_id: number | null;
  event_name: string | null;
  crm_event_id: string | null;
  event_date: string | null;
  brand: string | null;
  source: AlertSource;
  notified: boolean;
  notify_error: string;
}

export interface AlertDetail {
  alert: Alert;
  guide: { label: string; what: string; fix_in: string; after: string };
  event: {
    id: number;
    crm_event_id: string;
    event_name: string;
    event_date: string | null;
    brand: string;
    status: string;
    event_type: string;
    billing_model: string;
    final_invoice_amount: number | null;
  } | null;
  can_rerun: boolean;
}

export interface TelegramSettings {
  enabled: boolean;
  chat_ids: string[];
  dashboard_url: string;
  /** The token itself is never returned — only whether one is stored. */
  bot_token_set: boolean;
  bot_token: string;
  configured: boolean;
}

export interface TelegramSettingsInput {
  enabled: boolean;
  chat_ids: string[];
  dashboard_url: string;
  /** Omit to keep the stored token; "" clears it. */
  bot_token?: string;
}

export interface TelegramTestResult {
  ok: boolean;
  detail: string;
  sent: number;
  failed: number;
  skipped?: boolean;
  errors: string[];
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
  event_date: string | null;
  event_name: string;
  event_code: string | null;
  brand: string;
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
