"""The "Open in Kona OS" deep link on the event detail page.

Every figure the dashboard shows is derived from the KonaOS notes, so checking
one used to mean searching the KonaOS Events grid by hand for the same booking.
The link closes that loop — which makes its URL shape load-bearing: a wrong
fragment loads the admin shell with no event selected, which looks like the
link "not working" rather than an error anyone can trace.
"""
import os

os.environ.setdefault("CRM_PROVIDER", "mock")

from app.config import settings
from app.konaos.admin_links import (ADMIN_BASE_URL, event_admin_url,
                                    invoice_admin_url)
from app.models import Event, Invoice

# A real KonaOS event id, the 32-hex shape its API returns.
REAL_ID = "30c81c46a35ce411e5fbc1191a0a52ef"
# A real KonaOS invoice id, same shape (from the live invoice grid).
REAL_INVOICE_ID = "7031554e484648ceb97e1e167fdabeb2"


def test_url_is_the_admin_hash_route_the_grid_itself_uses():
    url = event_admin_url(REAL_ID)
    assert url == (
        f"{ADMIN_BASE_URL}/#/franchise/events/edit-event"
        f"?id={REAL_ID}&eventType=list"
    )


def test_no_id_means_no_link():
    assert event_admin_url("") is None
    assert event_admin_url(None) is None


def test_mock_events_are_not_offered_a_dead_link(monkeypatch):
    """Under CRM_PROVIDER=mock the ids are invented ("EVT-1001"), so a link
    would go nowhere. Offering nothing beats offering a broken button."""
    monkeypatch.setattr(settings, "crm_provider", "mock")
    assert Event(crm_event_id="EVT-1001").konaos_url is None


def test_konaos_events_get_the_link(monkeypatch):
    monkeypatch.setattr(settings, "crm_provider", "konaos")
    ev = Event(crm_event_id=REAL_ID)
    assert ev.konaos_url == event_admin_url(REAL_ID)


def test_invoice_url_is_the_route_the_admin_grid_navigates_to():
    """Taken from the admin app's own router, not guessed:
    router.navigate(["franchise/invoice/invoice-details"], {queryParams: {id}}).
    A wrong fragment loads the invoice shell with nothing selected, which reads
    as a broken link rather than an error anyone can trace."""
    assert invoice_admin_url(REAL_INVOICE_ID) == (
        f"{ADMIN_BASE_URL}/#/franchise/invoice/invoice-details?id={REAL_INVOICE_ID}"
    )


def test_a_draft_that_never_reached_konaos_gets_no_invoice_link(monkeypatch):
    """crm_invoice_id is empty on a dry run or a failed create — there is no
    document at the other end, so no button."""
    monkeypatch.setattr(settings, "crm_provider", "konaos")
    assert Invoice(crm_invoice_id=None).konaos_url is None
    assert Invoice(crm_invoice_id="").konaos_url is None


def test_mock_invoices_are_not_offered_a_dead_link(monkeypatch):
    monkeypatch.setattr(settings, "crm_provider", "mock")
    assert Invoice(crm_invoice_id=REAL_INVOICE_ID).konaos_url is None


def test_konaos_invoices_get_the_link(monkeypatch):
    monkeypatch.setattr(settings, "crm_provider", "konaos")
    assert Invoice(crm_invoice_id=REAL_INVOICE_ID).konaos_url == \
        invoice_admin_url(REAL_INVOICE_ID)


def test_invoice_schema_carries_the_link(monkeypatch):
    """Same from_attributes hop as the event link — the routes return the ORM
    object and never mention konaos_url."""
    from app.schemas.event import InvoiceOut

    monkeypatch.setattr(settings, "crm_provider", "konaos")
    inv = Invoice(
        id=1, event_id=1, crm_invoice_id=REAL_INVOICE_ID, invoice_number="00849",
        title="ThriftBooks", invoice_type="Invoice", status="draft",
        grand_total=136.40, subtotal=124.0, tax_amount=7.44, due_amount=136.40,
        has_variance=False, variance_amount=0.0, payload={},
    )
    from datetime import datetime, timezone
    inv.created_at = datetime.now(timezone.utc)

    out = InvoiceOut.model_validate(inv)
    assert out.konaos_url and REAL_INVOICE_ID in out.konaos_url


def test_detail_schema_carries_the_link(monkeypatch):
    """The property has to survive Pydantic's from_attributes conversion — the
    route returns the ORM object and never mentions konaos_url."""
    from app.schemas.event import EventDetail

    monkeypatch.setattr(settings, "crm_provider", "konaos")
    ev = Event(
        id=1, crm_event_id=REAL_ID, event_name="ThriftBooks", brand="Kona Ice",
        final_status="confirmed", event_type="package",
        billing_model="PACKAGE_PER_SERVING", final_invoice_amount=0.0,
        status="needs_review", status_reason="", raw={}, cleaned={}, classification={},
        square={}, calculations={},
    )
    from datetime import datetime, timezone
    ev.created_at = ev.updated_at = datetime.now(timezone.utc)

    out = EventDetail.model_validate(ev)
    assert out.konaos_url and REAL_ID in out.konaos_url
