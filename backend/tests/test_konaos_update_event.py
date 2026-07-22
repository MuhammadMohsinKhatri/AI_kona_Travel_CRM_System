"""Regression tests for the equipment/staff wipe bug (2026-07-21) AND its
first attempted fix, which was itself wrong (2026-07-22 correction).

A real captured KonaOS response (spec/openapi-devtools-spec.json in the API-
contract repo) shows eventAssetsList / eventStaffList / eventTemplatesDtoList /
itemsDtoList / tags / eventBannerFiles are ALWAYS null on a genuine GET,
alongside the real data under eventAssetsDtoList/eventStaffsDtoList — this
endpoint doesn't manage assignment through those keys at all. The original bug
was defaulting them to `[]` (KonaOS reads an empty list as "clear the
assignment"). The first fix instead populated them with the real GET-shaped
data, which is the wrong shape for whatever this endpoint expects and made
KonaOS reject the whole request (main.invalidJsonError) on every event with
any equipment/staff assigned. The correct fix, proven by the same precedent as
the already-working invoiceStatus drop: never send these six keys at all."""
import asyncio

from app.konaos.client import KonaosClient

DROPPED_FIELDS = (
    "eventAssetsList", "eventStaffList", "eventTemplatesDtoList",
    "itemsDtoList", "tags", "eventBannerFiles",
)


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code
        self.text = "{}"

    def json(self):
        return self._json

    def raise_for_status(self):
        pass


def _run_update(existing, **kwargs):
    kc = KonaosClient()
    captured = {}

    async def fake_get_event_details(event_id, deleted=None):
        return dict(existing)

    async def fake_make_request(method, path, json=None, **_kw):
        captured["payload"] = json
        return _FakeResponse({"success": True})

    kc.get_event_details = fake_get_event_details
    kc._make_request = fake_make_request

    result = asyncio.run(kc.update_event(existing["id"], **kwargs))
    return captured["payload"], result


def test_update_event_never_sends_the_six_always_null_fields_with_real_assignment():
    """An event with real equipment + staff assigned must not have those
    keys touched at all — dropped, not echoed, not emptied."""
    existing = {
        "id": "evt-1", "name": "Test Event", "businessName": "Test Biz",
        "eventAssetsDtoList": [{"assetId": "KEV1", "assetName": "KEV1"}],
        "eventStaffsDtoList": [{"staffId": "s1", "firstName": "Jane"}],
    }
    payload, result = _run_update(existing, ccAmount=100.0)

    for field in DROPPED_FIELDS:
        assert field not in payload, f"{field} must be absent from the request body"
    # Financial kwargs still merge in as before.
    assert payload["ccAmount"] == 100.0
    # Diagnostic counts reflect what existed pre-write (guaranteed untouched).
    assert result["_equipment_preserved"] == 1
    assert result["_staff_preserved"] == 1


def test_update_event_never_sends_them_when_truly_unassigned_either():
    """No equipment/staff on either side — still absent, not an empty list."""
    existing = {"id": "evt-3", "name": "Bare Event", "businessName": "Biz"}
    payload, result = _run_update(existing, ccAmount=5.0)

    for field in DROPPED_FIELDS:
        assert field not in payload
    assert result["_equipment_preserved"] == 0
    assert result["_staff_preserved"] == 0


def test_update_event_strips_even_an_explicit_kwargs_override():
    """This endpoint doesn't manage assignment through these keys at all —
    an explicit attempt to set eventAssetsList is stripped just like any
    other value, not treated as a deliberate reassignment to honor."""
    existing = {
        "id": "evt-2", "name": "Test Event 2", "businessName": "Test Biz 2",
        "eventAssetsDtoList": [{"assetId": "KEV1", "assetName": "KEV1"}],
    }
    payload, _ = _run_update(existing, eventAssetsList=[{"assetId": "KEV9"}])

    assert "eventAssetsList" not in payload
