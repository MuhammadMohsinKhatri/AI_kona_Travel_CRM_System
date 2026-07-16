# Pipeline — n8n → FastAPI mapping

This document maps the original **"data entry workflow Production"** n8n graph
(84 nodes) onto the modules in this codebase, and documents the business rules
that were ported verbatim.

## Flow overview

```
CRM.list_events ─► for each event ─► CRM.get_event ─► clean_event ─► gate(confirmed|completed)
   │
   └─► classify (LLM) ─► map_equipment + Square.search_orders ─► calculate_invoice
          │
          └─► build_invoice_payload ─► delete stale draft ─► create draft ─► update_event
          └─► check_alerts ─► persist alerts + Telegram notify
          └─► Sheets.append_row (monthly)
```

Orchestrated by [`app/core/pipeline.py`](../backend/app/core/pipeline.py) as
**eight batch phases** (fetch → clean → classify → square → calculate → invoice →
alerts → report). Each phase writes its status to `PipelineRun.progress`
(`[{key, label, status, detail}]`), which the dashboard polls to render live
step-by-step progress. Runs execute non-blocking: a FastAPI background task
(`PIPELINE_RUN_INLINE=true`) or the Celery worker (`false`), plus a nightly beat
schedule. Per-event failures are isolated — the event is marked `error` and
dropped from remaining phases without failing the run.

The CRM client (`CRM_PROVIDER=konaos`) is the in-process KonaOS client
(`backend/app/konaos/`) talking directly to `api.konaos.com`: events come from
the paginated grid-data endpoint (limit ≤ 100) filtered by epoch-ms
`fromDate`/`toDate` on `startDateTime` — a date-scoped run passes the target
date's NY-local bounds. The same client powers the `/api/konaos/*` endpoints
and the New Event form.

## Node → module map

| n8n node(s) | Ported to | Notes |
|---|---|---|
| `Get Events1`, `get event data1` | `integrations.*.CRMClient.list_events / get_event` | Kona CRM `/events` |
| `check if event is confirmed or completed` | `core.event_cleaner.is_confirmed_or_completed` | gate |
| `clean event data`, `CLEAN EVENT DATA1/5` | `core.event_cleaner.clean_event` | EDT tz, staff/equipment/contacts flatten |
| `AI analyzer`, `AI analyzer7` | `integrations.*.Classifier.classify` + `core/prompts/classifier_v8.md` | GPT-5.1, v8.0 schema |
| `Kona/Travellin Tom mapping equipments with device ids` | `core.equipment.map_equipment` | device-id lookup + driver-vs-assigned audit |
| `Search square orders`, `extracting all payment ids`, `Aggregate*`, `Call 'Aggregate square payments'` | `integrations.*.SquareClient.search_orders` | per-brand order/payment aggregation |
| `calculations1/3` | `core.billing.calculate_invoice` | **the 11-model engine** (v2.3) |
| `create invoice draft9/11`, `Restore Payload*`, `ROUTER*` | `core.invoice_builder.build_invoice_payload` | line items per model, INVOICE/HYBRID only |
| `Get Existing Invoices`, `Delete Previous Draft`, `create invoice draft1/10`, `Check Existing Invoice*` | `core.pipeline._replace_draft` | delete stale draft → create fresh |
| `update event2/3` | `CRMClient.update_event` | writes invoice info back to the event |
| `check alerts`, `Check Alerts`, `Alert code*`, `Has Alerts?*` | `core.alerts.check_alerts` | financial alert engine (v4.6) |
| `Send Telegram Alert*` | `integrations.*.Notifier.send` | Markdown alert message |
| `Months Sheet*`, `Duplicate Template Sheet*`, `Append or update row in sheet*` | `integrations.*.SheetsClient.append_row` | monthly reporting tab |
| `Generate Daily Summary Report1` | (reporting hook — extend `SheetsClient`/`Notifier`) | not yet ported; see Roadmap |
| `Loop Over events`, `splitInBatches`, `Switch*`, `If*` | control flow in `pipeline.run_pipeline` | |

## Billing models (core/billing.py)

Eleven models, each computing `subtotal` then applying **6% tax** (when taxable)
and a **4% CC/processing fee** that always applies:

`INVOICE_PER_SERVING`, `INVOICE_BASE_FEE_PLUS_SERVINGS`, `INVOICE_FIXED_PACKAGE`,
`INVOICE_HOURLY`, `SELLING_OPEN`, `SELLING_WITH_GIVEBACK`, `MIN_GUARANTEE_HOURLY`,
`MIN_GUARANTEE_FLAT`, `HYBRID_HOST_BASE_PLUS_GUEST_EXTRA`,
`HYBRID_HOST_SUBSIDY_PLUS_GUEST_PAYMENT`, `HYBRID_SELLING_PLUS_MIN_GUARANTEE`.

Preserved invariants:
- No discount math in the engine — `BASE_AMOUNT` is already post-discount.
- MG models bill the **guaranteed floor + location fee**, independent of servings.
- An admin-written `CHECK_INVOICE_AMOUNT` overrides the calculated amount, and the
  difference is recorded as `VARIANCE_AMOUNT` (surfaced in the UI/Invoices).

## Alert engine (core/alerts.py)

Interprets the classifier's `ALERT` keys (e.g. `MISSING_RATE_PER_SERVING`,
`TAX_EXEMPT_UNVERIFIED`, `PAYMENT_STATUS_UNCLEAR`, `UNCONFIRMED_DISCOUNT_OR_WAIVER`)
plus calculation validations (e.g. "revenue collected but invoice is $0"), returning
`CRITICAL/HIGH/MEDIUM/LOW` alerts. Selling events suppress rate/serving alerts because
Square supplies the actuals.

## Roadmap / not-yet-ported

- **Daily summary report** (`Generate Daily Summary Report1`) — a scheduled digest;
  add as a second Celery beat task calling `Notifier`/`SheetsClient`.
- **Google Sheet template duplication** — the live `SheetsClient` appends rows; the
  monthly-tab auto-create from a template sheet is stubbed to `append_row`.
- Multi-brand Square account routing is by brand string; extend `SquareClient` if more
  brands/locations are added.
