"""Integration interfaces.

Every external system the pipeline touches is defined here as an abstract
protocol. Concrete mock and live implementations live alongside; the factory
(`app.integrations.factory`) picks one per the ``*_PROVIDER`` env var, so the
pipeline code depends only on these interfaces.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class CRMClient(ABC):
    @abstractmethod
    def list_events(
        self, from_ms: int | None = None, to_ms: int | None = None
    ) -> list[dict[str, Any]]:
        """Return the event grid (summary rows with at least an ``id``).

        ``from_ms``/``to_ms`` are optional epoch-ms bounds on the event start
        (the Kona OS API filters by ``startDateTime``).
        """

    @abstractmethod
    def get_event(self, event_id: str) -> dict[str, Any]:
        """Return the full event payload for one event id."""

    @abstractmethod
    def list_invoices(self) -> list[dict[str, Any]]:
        """Existing invoices grid — used to find/delete stale drafts."""

    @abstractmethod
    def create_invoice(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create an invoice draft; return CRM response incl. an id."""

    @abstractmethod
    def delete_invoice(self, invoice_id: str) -> None:
        ...

    @abstractmethod
    def update_event(self, event_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        ...


class SquareClient(ABC):
    @abstractmethod
    def search_orders(
        self, brand: str, device_id: str | None, date_iso: str | None
    ) -> dict[str, Any]:
        """Search Square orders for a brand/device/date. Returns aggregate totals."""


class Classifier(ABC):
    @abstractmethod
    def classify(self, cleaned_event: dict[str, Any]) -> dict[str, Any]:
        """Return the flat classification_output dict for one cleaned event."""


class SheetsClient(ABC):
    @abstractmethod
    def append_row(self, brand: str, row: dict[str, Any]) -> None:
        ...


class Notifier(ABC):
    @abstractmethod
    def send(self, message: str) -> None:
        ...
