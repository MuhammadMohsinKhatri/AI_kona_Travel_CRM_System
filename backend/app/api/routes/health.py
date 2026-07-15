from __future__ import annotations

from fastapi import APIRouter

from app.config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "environment": settings.environment,
        "providers": {
            "crm": settings.crm_provider,
            "square": settings.square_provider,
            "openai": settings.openai_provider,
            "sheets": settings.sheets_provider,
            "telegram": settings.telegram_provider,
        },
    }
