"""Regression test for the equipment/staff wipe bug: KonaOS's read and write
DTOs use different field names for equipment/staff assignment
(eventAssetsDtoList/eventStaffsDtoList on GET vs eventAssetsList/eventStaffList
on PUT). update_event's read-modify-write must map the real data across
instead of letting the "ensure arrays are lists" defaulting fill it with []
(which KonaOS reads as "unassign everything")."""
import asyncio

from app.konaos.client import KonaosClient


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code
        self.text = "{}"

    def json(self):
        return self._json

    def raise_for_status(self):
        pass


def test_update_event_preserves_equipment_and_staff_assignment():
    kc = KonaosClient()
    existing = {
        "id": "evt-1",
        "name": "Test Event",
        "businessName": "Test Biz",
        # GET-shaped keys — this is what a real KonaOS event detail carries.
        "eventAssetsDtoList": [{"assetId": "KEV1", "assetName": "KEV1"}],
        "eventStaffsDtoList": [{"staffId": "s1", "firstName": "Jane"}],
    }
    captured = {}

    async def fake_get_event_details(event_id, deleted=None):
        return dict(existing)

    async def fake_make_request(method, path, json=None, **kwargs):
        captured["payload"] = json
        return _FakeResponse({"success": True})

    kc.get_event_details = fake_get_event_details
    kc._make_request = fake_make_request

    asyncio.run(kc.update_event("evt-1", ccAmount=100.0))

    payload = captured["payload"]
    # The write-shaped keys must carry the real assignment across, not [].
    assert payload["eventAssetsList"] == existing["eventAssetsDtoList"]
    assert payload["eventStaffList"] == existing["eventStaffsDtoList"]
    # Financial kwargs still merge in as before.
    assert payload["ccAmount"] == 100.0


def test_update_event_respects_explicit_asset_override():
    """A caller deliberately reassigning equipment (passing eventAssetsList
    explicitly) must not be overridden by the GET-shaped data."""
    kc = KonaosClient()
    existing = {
        "id": "evt-2",
        "name": "Test Event 2",
        "businessName": "Test Biz 2",
        "eventAssetsDtoList": [{"assetId": "KEV1", "assetName": "KEV1"}],
    }
    captured = {}

    async def fake_get_event_details(event_id, deleted=None):
        return dict(existing)

    async def fake_make_request(method, path, json=None, **kwargs):
        captured["payload"] = json
        return _FakeResponse({"success": True})

    kc.get_event_details = fake_get_event_details
    kc._make_request = fake_make_request

    asyncio.run(kc.update_event("evt-2", eventAssetsList=[{"assetId": "KEV9"}]))

    assert captured["payload"]["eventAssetsList"] == [{"assetId": "KEV9"}]


def test_update_event_defaults_to_empty_when_truly_unassigned():
    """An event with no equipment/staff on either side stays an empty list,
    not a KeyError or None."""
    kc = KonaosClient()
    existing = {"id": "evt-3", "name": "Bare Event", "businessName": "Biz"}
    captured = {}

    async def fake_get_event_details(event_id, deleted=None):
        return dict(existing)

    async def fake_make_request(method, path, json=None, **kwargs):
        captured["payload"] = json
        return _FakeResponse({"success": True})

    kc.get_event_details = fake_get_event_details
    kc._make_request = fake_make_request

    asyncio.run(kc.update_event("evt-3", ccAmount=5.0))

    assert captured["payload"]["eventAssetsList"] == []
    assert captured["payload"]["eventStaffList"] == []
