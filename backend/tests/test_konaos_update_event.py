"""Regression tests for the equipment/staff wipe bug (2026-07-21) and its
first TWO attempted fixes, both wrong in different ways (2026-07-22).

A real captured KonaOS response (spec/openapi-devtools-spec.json in the API-
contract repo) shows eventAssetsList / eventStaffList / eventTemplatesDtoList /
itemsDtoList / tags / eventBannerFiles are ALWAYS present (never omitted —
they're in the parent Event object's "required" list) but always `null`,
alongside the real data under eventAssetsDtoList/eventStaffsDtoList. This
endpoint doesn't manage assignment through these six keys at all.

Three states were tried:
  1. (pre-incident) Defaulted to `[]` when None — KonaOS reads an empty list
     as "explicitly clear," wiping real equipment/staff.
  2. (fix #1, reverted) Populated with the real GET-shaped data — wrong
     shape, KonaOS 400'd main.invalidJsonError on every event with any
     equipment/staff assigned.
  3. (fix #2, reverted) Dropped the keys entirely (absent) — a shape KonaOS
     itself never produces (they're always present-but-null on read), and
     every update_event call 500'd main.internalServerError instead.

The correct fix: keep the key present, value explicit null — exactly what
KonaOS's own system always sends."""
import asyncio

from app.konaos.client import KonaosClient

NULLED_FIELDS = (
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


def test_update_event_sends_explicit_null_with_real_assignment():
    """An event with real equipment + staff assigned must have those keys
    sent as explicit null — present in the body (matching what KonaOS's own
    system always sends), never as a list, never omitted."""
    existing = {
        "id": "evt-1", "name": "Test Event", "businessName": "Test Biz",
        "eventAssetsDtoList": [{"assetId": "KEV1", "assetName": "KEV1"}],
        "eventStaffsDtoList": [{"staffId": "s1", "firstName": "Jane"}],
    }
    payload, result = _run_update(existing, ccAmount=100.0)

    for field in NULLED_FIELDS:
        assert field in payload, f"{field} must be present in the request body"
        assert payload[field] is None, f"{field} must be explicit null, not a list"
    # Financial kwargs still merge in as before.
    assert payload["ccAmount"] == 100.0
    # Diagnostic counts + names reflect what existed pre-write (guaranteed
    # untouched) — names so an admin can visually confirm the actual
    # truck/kiosk/person, not just a number.
    assert result["_equipment_preserved"] == 1
    assert result["_equipment_names"] == ["KEV1"]
    assert result["_staff_preserved"] == 1
    assert result["_staff_names"] == ["Jane"]


def test_update_event_sends_explicit_null_when_truly_unassigned_too():
    """No equipment/staff on either side — still explicit null, not `[]`."""
    existing = {"id": "evt-3", "name": "Bare Event", "businessName": "Biz"}
    payload, result = _run_update(existing, ccAmount=5.0)

    for field in NULLED_FIELDS:
        assert field in payload
        assert payload[field] is None
    assert result["_equipment_preserved"] == 0
    assert result["_equipment_names"] == []
    assert result["_staff_preserved"] == 0
    assert result["_staff_names"] == []


def test_update_event_nulls_out_even_an_explicit_kwargs_override():
    """This endpoint doesn't manage assignment through these keys at all —
    an explicit attempt to set eventAssetsList is overwritten to null just
    like any other value, not treated as a deliberate reassignment to
    honor. (The safety net would refuse the call outright if this ever
    resulted in a list reaching the request instead.)"""
    existing = {
        "id": "evt-2", "name": "Test Event 2", "businessName": "Test Biz 2",
        "eventAssetsDtoList": [{"assetId": "KEV1", "assetName": "KEV1"}],
    }
    payload, _ = _run_update(existing, eventAssetsList=[{"assetId": "KEV9"}])

    assert payload["eventAssetsList"] is None
