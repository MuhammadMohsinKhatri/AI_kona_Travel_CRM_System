from __future__ import annotations

import os

from fastapi import APIRouter

from app.config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "environment": settings.environment,
        # The commit this container was built from (CI stamps it into the image;
        # "dev" locally). A backend-only deploy changes no frontend asset hash
        # and no route signature, so this is the only thing that distinguishes
        # "the fix is live" from "still waiting" without shell access to the box.
        "build": os.getenv("GIT_SHA", "dev")[:7],
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
