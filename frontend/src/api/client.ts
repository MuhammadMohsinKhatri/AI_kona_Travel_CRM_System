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
      /** Short commit the running backend was built from ("dev" locally). */
      build: string;
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
  runs: (params: Record<string, string> = {}) =>
    request<Page<PipelineRun>>("/api/pipeline/runs?" + new URLSearchParams(params)),
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
  /** Set the giveback percentage agreed with the venue (10 = 10%) and re-price
   *  the event. Needed because a giveback is often agreed long in advance and
   *  never written into the notes, so the classifier records 0 and the amount
   *  owed goes missing. Like cash, the override survives later pipeline runs. */
  setEventGiveback: (crmEventId: string, percent: number, by = "") =>
    request<GivebackUpdateResult>(
      `/api/financials/by-event/${encodeURIComponent(crmEventId)}/giveback`,
      {
        method: "PATCH",
        body: JSON.stringify({ giveback_percent: percent, source: "manual", by }),
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
  // ── Recording payments that arrive off-system ──────────────────────────
  // The intake calls settle what they can on their own and hand back what they
  // won't. `applied` on a result means it is already done in KonaOS; absent,
  // `held_because` says what stopped it and the line waits for a person.
  // The `rematch*` calls never write — they are the correction path, safe to
  // repeat after every edit.
  /** Read a photographed check, match it, and settle it if the amount agrees
   *  exactly with one open invoice. Writes when it is sure. */
  reviewCheckPhoto: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<CheckReview>("/api/intake/check", { method: "POST", body: form });
  },
  /** Re-match after correcting a misread field, or against an invoice the
   *  reviewer picked off the screen (`invoice_id` beats the score). */
  rematchCheck: (body: CheckRematchInput) =>
    request<CheckReview>("/api/intake/check/rematch", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /** Transcribe dictated takings, match every event mentioned, and post the
   *  ones that matched unambiguously. One recording, a day's takings done. */
  reviewCashVoice: (file: File, defaultDate = "") => {
    const form = new FormData();
    form.append("file", file);
    form.append("default_date", defaultDate);
    return request<CashReviewResponse>("/api/intake/cash/voice", {
      method: "POST",
      body: form,
    });
  },
  reviewCashText: (transcript: string, defaultDate = "") =>
    request<CashReviewResponse>("/api/intake/cash/text", {
      method: "POST",
      body: JSON.stringify({ transcript, default_date: defaultDate }),
    }),
  rematchCash: (body: CashRematchInput) =>
    request<CashReview>("/api/intake/cash/rematch", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /** Apply the whole approved batch. The server recomputes every derived
   *  figure — this sends only which invoice/event, and how much. */
  applyPayments: (items: ApplyItem[]) =>
    request<ApplyResponse>("/api/intake/apply", {
      method: "POST",
      body: JSON.stringify({ items }),
    }),
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

export interface GivebackUpdateResult {
  crm_event_id: string;
  giveback_percent: number;
  giveback_amount: number;
  previous_giveback_amount: number;
  source: "api" | "manual" | "ai";
  /** The single-event run that re-priced this event. Follow it on Automation Runs. */
  repriced_run_id?: number | null;
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

/** One recorded change to an event's cash figure, from the CRM audit trail.
 *  `by` is whoever the caller identified itself as — a person's name when typed
 *  in the dashboard, or the automation's name when posted over the API. It is
 *  "" when the caller didn't say, so `source` is the fallback. */
export interface CashLogEntry {
  at: string | null;
  by: string;
  source: string;
  previous: number | null;
  amount: number | null;
}

export interface FinancialRow {
  id: number;
  event_id: number;
  crm_event_id: string;
  /** Per-field provenance, keyed by field name (cash_collected, deposit, …). */
  sources?: Record<string, FieldSource>;
  /** Recent cash_updated log entries (newest first) — who set the figure, when,
   *  and what it was before. Empty when nobody has posted a cash figure, in
   *  which case whatever is shown came from the classifier reading the notes. */
  cash_history?: CashLogEntry[];
  awaiting_cash?: boolean;
  minimum_required?: number;
  event_date: string | null;
  /** Wall-clock ISO start/end from the event's cleaned payload (null for
   *  sheet-imported placeholders that never went through the pipeline). */
  event_started: string | null;
  event_ended: string | null;
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

// ── Recording payments ────────────────────────────────────────────────────

/** What the vision model made of the check. Every field is a suggestion the
 *  reviewer can overwrite — `confidence` is the model's own, not a promise. */
export interface CheckDetails {
  payer_name: string;
  payer_address: string;
  amount: number;
  check_date: string;
  check_number: string;
  memo: string;
  confidence: string;
  notes: string;
  error: string;
}

/** An invoice the check might be paying, with the scoring that put it here.
 *  Shown even when nothing matched, so a failure says what it nearly picked. */
export interface InvoiceCandidate {
  id: string;
  invoice_number: string;
  business_name: string;
  /** The event this invoice bills for — what actually tells two invoices for
   *  the same business apart. */
  event_name: string;
  event_date: string;
  invoice_date: string;
  status: string;
  grand_total: number;
  /** What this invoice comes to with the 4% card fee removed — i.e. what a
   *  cheque for it is normally written for. null when it can't be computed. */
  total_without_fee: number | null;
  score: number;
  flags: string[];
}

/** Exactly what applying this check would change. Nothing has happened yet. */
export interface SettlePlan {
  invoice_id: string;
  invoice_number: string;
  event_id: string;
  business_name: string;
  check_amount: number;
  /** As KonaOS holds it now, 4% processing fee included. */
  invoice_total: number;
  /** The 4% that comes off because this is a check, not a card. */
  cc_fee_removed: number;
  /** What the client actually owes once the fee is off — the figure the
   *  office quotes, and therefore what a correct check is written for. */
  amount_due_after_fee: number;
  variance: number;
  status: "exact" | "underpaid" | "overpaid";
  fully_paid: boolean;
  warnings: string[];
}

export interface CheckReview {
  kind: "check";
  /** True when Apply has something unambiguous to do. */
  ready: boolean;
  /** Set when the upload settled itself: the fee is off, the payment is
   *  recorded, and there is nothing left to confirm. */
  applied: ApplyResult | null;
  /** Why it did NOT settle itself — shown so a held check explains itself
   *  rather than just sitting there. */
  held_because: string;
  check: CheckDetails;
  reason: string;
  needs_choice: boolean;
  candidates: InvoiceCandidate[];
  plan: SettlePlan | null;
}

export interface EventCandidate {
  id: string;
  name: string;
  score: number;
  flags: string[];
  event_date: string;
  city: string;
}

export interface CashReview {
  kind: "cash";
  ready: boolean;
  /** Set when this line posted itself off the recording. */
  applied: ApplyResult | null;
  held_because: string;
  heard: { query: string; amount: number; brand: string; date: string };
  reason: string;
  needs_choice: boolean;
  /** Why this line can't be applied even though an event was matched — no
   *  ledger row yet, or no amount heard. Empty when it's fine. */
  blocked: string;
  candidates: EventCandidate[];
  event: {
    crm_event_id: string;
    event_name: string;
    event_date: string | null;
    brand: string;
    billing_model: string;
  } | null;
  previous_cash: number;
  ledger_found: boolean;
}

export interface CashReviewResponse {
  transcript: string;
  notes: string;
  error: string;
  items: CashReview[];
}

export interface CheckRematchInput {
  payer_name: string;
  amount: number;
  check_date?: string;
  check_number?: string;
  memo?: string;
  /** Set when the reviewer picked an invoice by hand. */
  invoice_id?: string;
}

export interface CashRematchInput {
  query: string;
  amount: number;
  brand?: string;
  date?: string;
  /** Set when the reviewer picked an event by hand. */
  crm_event_id?: string;
}

/** One approved line. Deliberately thin: which invoice or event, and how
 *  much. Every derived figure is recomputed server-side. */
export interface ApplyItem {
  kind: "check" | "cash";
  amount: number;
  invoice_id?: string;
  payer_name?: string;
  crm_event_id?: string;
}

export interface ApplyResult {
  ok: boolean;
  kind: "check" | "cash";
  summary: string;
  invoice_id?: string;
  crm_event_id?: string;
  dry_run?: boolean;
  warnings?: string[];
  detail?: Record<string, unknown>;
}

export interface ApplyResponse {
  applied: number;
  failed: number;
  /** True when the system is in dry-run mode: nothing was written anywhere. */
  dry_run: boolean;
  results: ApplyResult[];
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
  /** Deep link to this invoice in Kona OS. null when the draft never reached
   *  Kona OS (dry run, or a create that failed), so there's nothing to open. */
  konaos_url: string | null;
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
  /** Deep link to this event in KonaOS. null when the events didn't come from
   *  KonaOS (mock dataset), in which case no link is offered. */
  konaos_url: string | null;
  raw: Record<string, unknown>;
  cleaned: Record<string, unknown>;
  classification: Record<string, unknown>;
  square: Record<string, unknown>;
  calculations: Record<string, unknown>;
  invoices: Invoice[];
  alerts: Alert[];
}
