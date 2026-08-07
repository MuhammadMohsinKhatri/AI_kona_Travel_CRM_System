"""Application settings loaded from environment / .env."""
from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # App
    app_name: str = "Conbyt AI Automation Financial System"
    environment: str = "development"
    secret_key: str = "change-me"
    access_token_expire_minutes: int = 480
    algorithm: str = "HS256"
    # Comma-separated list of allowed origins (kept as str to avoid
    # pydantic-settings' JSON pre-parsing of complex env values).
    backend_cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Database
    database_url: str = "sqlite:///./konaice.db"

    # Celery
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    pipeline_run_inline: bool = True
    # Safety switch: when true, the pipeline computes everything and stores
    # invoices locally but performs NO writes to the CRM (no draft create/
    # delete, no event update). Use for first runs against production.
    pipeline_dry_run: bool = False

    # Seed admin
    first_admin_email: str = "admin@konaice.com"
    first_admin_password: str = "changeme"

    # Providers — CRM: "mock" | "konaos" (in-process KonaOS client);
    # the rest: "mock" | "live"
    crm_provider: str = "mock"
    square_provider: str = "mock"
    openai_provider: str = "mock"
    telegram_provider: str = "mock"

    # Samsara — the trucks. Read-only here: where a vehicle is, and how much
    # fuel it has. Blank disables the fleet tools and the gas alert cleanly;
    # everything else carries on (see aimee/registry.py on failure shape).
    samsara_api_base: str = "https://api.samsara.com"
    samsara_api_token: str = ""

    # Google Maps — geocoding, directions and Street View, for the route and
    # ETA tools. The key stays SERVER-side: map imagery is proxied through
    # /api/aimee/media rather than embedded in a browser URL, because a key in
    # a page an office shares around is a key that gets scraped and billed.
    google_maps_api_base: str = "https://maps.googleapis.com/maps/api"
    google_maps_api_key: str = ""
    # Where a truck starts from when no origin is given — the yard.
    fleet_home_address: str = "28 Alco Place, Lansdowne, MD 21227"

    # Square
    square_api_base: str = "https://connect.squareup.com"
    square_kona_token: str = ""
    square_tom_token: str = ""
    square_kona_location: str = "LGYP8DB54HMPV"
    square_tom_location: str = "LGWPJYFHY9AJD"
    # Timecard webhook (app/api/routes/webhooks.py). One signature key per
    # brand, because Kona and Tom are separate Square accounts with separate
    # subscriptions; which key verifies identifies the sender.
    #
    # square_webhook_url must match the notification URL registered in the
    # Square dashboard CHARACTER FOR CHARACTER — it is part of the signed
    # payload, so a trailing slash or an http/https mismatch fails every
    # request with no other symptom. Blank keys disable the endpoint entirely
    # (401 on everything), which is the safe default for a public route whose
    # only job is pushing messages to phones.
    square_webhook_url: str = ""
    square_kona_webhook_signature_key: str = ""
    square_tom_webhook_signature_key: str = ""

    # OpenAI
    openai_api_key: str = ""
    # gpt-5-mini: structured extraction from short notes doesn't need the
    # flagship model — mini is ~5x cheaper per token and accurate for this
    # task; the deterministic rule_classifier handles form-generated events
    # without any AI call at all.
    openai_model: str = "gpt-5-mini"
    # $ per 1M tokens — used to compute per-run AI cost shown in the dashboard.
    # Update if OpenAI changes pricing or you switch models.
    openai_input_cost_per_mtok: float = 0.25
    openai_output_cost_per_mtok: float = 2.0

    # Vision/speech intake (app/core/intake_readers.py: read_check, transcribe,
    # parse_cash_speech) always calls VISION_MODEL ("gpt-4o"), never OPENAI_MODEL
    # — a different, pricier model, so it needs its own $/Mtok to cost correctly
    # rather than borrowing the classifier's rate. Update if OpenAI's pricing or
    # VISION_MODEL changes.
    openai_vision_input_cost_per_mtok: float = 2.5
    openai_vision_output_cost_per_mtok: float = 10.0

    # Organization-level Admin API key (platform.openai.com -> Organization ->
    # Admin keys) — NOT the same credential as openai_api_key above, which is a
    # project key scoped to making completions. Only this key can read
    # /v1/organization/costs, which is what "remaining AI budget" is computed
    # against (see app/core/ai_budget.py). Leave blank to disable that feature
    # without affecting anything else.
    openai_admin_api_key: str = ""

    # Pre-invoice consistency gate (app/core/invariants.py). When a check fails
    # the event is held in needs_review and NO invoice is drafted, so a
    # misclassification cannot reach a client. Set false to fall back to
    # invoicing everything and treating violations as alerts only — the escape
    # hatch if the checks ever start holding correct events in bulk, since this
    # gate sits in front of billing and a bad rule would stall invoicing.
    invoice_gate_enabled: bool = True

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Legacy financial Google Sheets — CSV export URLs used by the one-click
    # importer on the Financials page (one per brand). The sheets are published
    # for CSV export, so no auth is needed. Override via env to point at a
    # different sheet/tab (…/export?format=csv&gid=<tab-gid>).
    financials_sheet_csv_url: str = (  # Kona Ice
        "https://docs.google.com/spreadsheets/d/"
        "1tuLWnWQTHErp50ITvhkOMdBRZ4xDlruISPjMIZ_W-Z0/export?format=csv&gid=1031520435"
    )
    financials_sheet_tom_csv_url: str = (  # Travelin Tom
        "https://docs.google.com/spreadsheets/d/"
        "1ntLyVH37MQypG7nLpnd6JTsaCI4UP1VbN3vfXVzzZhs/export?format=csv"
    )

    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.backend_cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
