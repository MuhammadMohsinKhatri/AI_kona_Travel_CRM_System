from __future__ import annotations

from fastapi import APIRouter

from app.config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "environment": settings.environment,
        # Surfaced in the dashboard: when true, invoice drafts are computed
        # and stored locally but NOT created in KonaOS.
        "pipeline_dry_run": settings.pipeline_dry_run,
        "providers": {
            "crm": settings.crm_provider,
            "square": settings.square_provider,
            "openai": settings.openai_provider,
            "telegram": settings.telegram_provider,
        },
    }
