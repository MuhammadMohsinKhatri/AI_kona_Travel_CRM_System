from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (alerts, auth, crm_audit, dashboard, events, financials,
                            health, invoices, pipeline)
from app.bootstrap import init_db
from app.config import settings
from app.konaos import router as konaos


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    konaos.init_konaos()
    yield
    await konaos.close_konaos()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="Conbyt AI Automation Financial System — Kona Ice / Travelin' Tom "
                "event → invoice automation.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(events.router)
app.include_router(invoices.router)
app.include_router(alerts.router)
app.include_router(pipeline.router)
app.include_router(financials.router)
app.include_router(crm_audit.router)
# KonaOS CRM endpoints (direct client for api.konaos.com)
app.include_router(konaos.router, prefix="/api/konaos", tags=["konaos"])


@app.get("/")
def root() -> dict:
    return {"service": settings.app_name, "docs": "/docs", "health": "/health"}
