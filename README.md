# Kona Ice / Travelin' Tom — Event → Invoice Automation

A full-stack replacement for the original n8n "data entry workflow Production" automation.
It ingests events from the Kona CRM, classifies each event's billing model with an LLM,
reconciles Square sales, computes the invoice, creates an invoice draft in the CRM, and
raises financial alerts — all exposed through a FastAPI backend and a React dashboard.

## Architecture

```
                         ┌──────────────────────────────────────────┐
                         │                React SPA                  │
                         │  login · dashboard · events · invoices    │
                         │              · alerts                     │
                         └───────────────────┬──────────────────────┘
                                             │ REST + JWT
                         ┌───────────────────▼──────────────────────┐
                         │               FastAPI API                 │
                         │  auth · events · invoices · alerts ·       │
                         │  pipeline trigger                          │
                         └──────┬─────────────────────────┬──────────┘
                                │                          │
                    ┌───────────▼──────────┐    ┌──────────▼───────────┐
                    │    PostgreSQL        │    │   Celery worker      │
                    │  events · invoices · │    │  (Redis broker)      │
                    │  alerts · runs·users │    │  pipeline execution  │
                    └──────────────────────┘    └──────────┬───────────┘
                                                           │
                    ┌──────────────────────────────────────▼───────────┐
                    │            Integration adapter layer              │
                    │  KonaCRM · Square · OpenAI · GoogleSheets ·        │
                    │  Telegram   (live OR mock, chosen per env var)    │
                    └───────────────────────────────────────────────────┘
```

The **pipeline** (`backend/app/core/pipeline.py`) is a faithful port of the n8n graph:

1. **Ingest** — pull events from Kona CRM, keep confirmed/completed ones.
2. **Clean** — normalize each event to EDT, flatten staff/equipment/contacts/financials.
3. **Classify** — GPT-5.1 resolves `EVENT_TYPE` + `BILLING_MODEL` and extracts ~50 billing fields from the notes.
4. **Reconcile Square** — map equipment → Square device IDs and pull matching orders/payments (per brand).
5. **Calculate** — the 11-model billing engine computes subtotal, tax (6%), 4% CC fee, deposits, balance, variance.
6. **Build & create invoice** — assemble line items, delete any stale draft, create a fresh draft in the CRM, update the event.
7. **Alerts** — evaluate the financial-alert rules and (optionally) push a Telegram summary.
8. **Report** — append the row to the monthly Google Sheet.

## Quick start (Docker)

```bash
cp backend/.env.example backend/.env      # defaults run fully mocked, no external keys needed
docker compose up --build
```

- API:      http://localhost:8000  (docs at `/docs`)
- Frontend: http://localhost:5173
- Seeded admin login: `admin@konaice.com` / `changeme`

Trigger a pipeline run from the dashboard ("Run pipeline") or:

```bash
curl -X POST http://localhost:8000/api/pipeline/run \
  -H "Authorization: Bearer <token>"
```

With the default `*_PROVIDER=mock` settings, the run generates realistic sample events end-to-end.

## Local dev without Docker

Backend:
```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
# in another shell (only needed for async pipeline runs):
celery -A app.tasks.celery_app.celery worker --loglevel=info -P solo
```

Frontend:
```bash
cd frontend
npm install
npm run dev
```

## Going live

Set the corresponding provider to `live` and supply credentials in `backend/.env`:

| Env var | Purpose |
|---|---|
| `CRM_PROVIDER=konaos` / `KONAOS_*` vars | KonaOS CRM events + invoices (direct, in-process) |
| `SQUARE_PROVIDER` / `SQUARE_KONA_TOKEN` / `SQUARE_TOM_TOKEN` | Square orders/payments (per brand) |
| `OPENAI_PROVIDER` / `OPENAI_API_KEY` / `OPENAI_MODEL` | Classifier LLM |
| `SHEETS_PROVIDER` / `GOOGLE_SERVICE_ACCOUNT_JSON` / `*_SHEET_ID` | Monthly reporting sheet |
| `TELEGRAM_PROVIDER` / `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Alert notifications |

Each integration is a small class behind an interface (`backend/app/integrations/`), so a live
provider is a drop-in replacement for the mock — the pipeline code never changes.

See [`backend/app/core/`](backend/app/core) for the ported business logic and
[`docs/PIPELINE.md`](docs/PIPELINE.md) for a node-by-node mapping from the original n8n workflow.

## Production deployment

For a VPS (e.g. Hostinger KVM 2) use the production stack — Caddy with automatic
HTTPS, Celery worker/beat, memory limits sized for a small box:

```bash
DOMAIN=ops.example.com docker compose -f docker-compose.prod.yml up -d --build
```

Full step-by-step guide: [`deploy/DEPLOY.md`](deploy/DEPLOY.md).
