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
    app_name: str = "Kona Ice Invoice Automation"
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

    # Providers: "mock" | "live"
    crm_provider: str = "mock"
    square_provider: str = "mock"
    openai_provider: str = "mock"
    sheets_provider: str = "mock"
    telegram_provider: str = "mock"

    # Kona CRM
    kona_crm_base_url: str = "https://konaoscrmsapis-production.up.railway.app"
    kona_crm_token: str = ""

    # Square
    square_api_base: str = "https://connect.squareup.com"
    square_kona_token: str = ""
    square_tom_token: str = ""
    square_kona_location: str = "LGYP8DB54HMPV"
    square_tom_location: str = "LGWPJYFHY9AJD"

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-5.1"
    # $ per 1M tokens — used to compute per-run AI cost shown in the dashboard.
    # Update if OpenAI changes pricing or you switch models.
    openai_input_cost_per_mtok: float = 1.25
    openai_output_cost_per_mtok: float = 10.0

    # Google Sheets
    google_service_account_json: str = ""
    kona_sheet_id: str = "1tuLWnWQTHErp50ITvhkOMdBRZ4xDlruISPjMIZ_W-Z0"
    tom_sheet_id: str = "1ntLyVH37MQypG7nLpnd6JTsaCI4UP1VbN3vfXVzzZhs"

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.backend_cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
