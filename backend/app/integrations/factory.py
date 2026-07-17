"""Provider factory — returns mock or live clients based on settings.

Cached so a single process reuses one instance per integration (important for
the mocks, which hold in-memory state).
"""
from __future__ import annotations

from functools import lru_cache

from app.config import settings
from app.integrations.base import (
    Classifier,
    CRMClient,
    Notifier,
    SquareClient,
)


@lru_cache
def get_crm() -> CRMClient:
    if settings.crm_provider == "konaos":
        # Direct in-process KonaOS client (app.konaos) — talks to api.konaos.com.
        from app.integrations.konaos_direct import KonaOSDirectCRMClient
        return KonaOSDirectCRMClient()
    from app.integrations.mocks import MockCRMClient
    return MockCRMClient()


@lru_cache
def get_square() -> SquareClient:
    if settings.square_provider == "live":
        from app.integrations.live import SquareLiveClient
        return SquareLiveClient()
    from app.integrations.mocks import MockSquareClient
    return MockSquareClient()


@lru_cache
def get_classifier() -> Classifier:
    if settings.openai_provider == "live":
        from app.integrations.live import OpenAIClassifier
        return OpenAIClassifier()
    from app.integrations.mocks import MockClassifier
    return MockClassifier()


@lru_cache
def get_notifier() -> Notifier:
    if settings.telegram_provider == "live":
        from app.integrations.live import TelegramNotifier
        return TelegramNotifier()
    from app.integrations.mocks import MockNotifier
    return MockNotifier()
