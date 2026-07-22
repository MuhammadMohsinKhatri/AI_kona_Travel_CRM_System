"""Regression tests for the equipment-wipe bug (2026-07-21) and the several
attempted fixes that followed (2026-07-22).

A real captured KonaOS response (spec/openapi-devtools-spec.json in the API-
contract repo) shows eventAssetsList / eventStaffList / eventTemplatesDtoList /
itemsDtoList / tags / eventBannerFiles are always present but `null` on a GET,
with the real assignment under eventAssetsDtoList / eventStaffsDtoList.

States tried on the PUT for these six keys:
  1. (pre-incident) `[]` for all — KonaOS reads an empty eventAssetsList as
     "clear equipment," wiping it (the 2026-07-21 incident).
  2. (reverted) eventAssetsList populated with the FULL GET-shaped objects —
     400 main.invalidJsonError.
  3. (reverted) keys dropped entirely — 500 main.internalServerError.
  4. (reverted) all six sent as explicit `null` — STILL 500s for
     selling/pending events.

Correct fix (matches the legacy n8n "update event3" body that ran nightly in
production): re-assert eventAssetsList from the event's REAL equipment in the
minimal {"assetId": …} shape, and send the other five as []. Empty is only
destructive for eventAssetsList, so the other five as [] are a safe no-op.
"""
import asyncio

import pytest

from app.konaos.client import KonaosClient

EMPTIED_FIELDS = (
    "eventStaffList", "eventTemplatesDtoList",
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


def test_update_event_reasserts_real_equipment():
    """An event with real equipment assigned re-asserts it in the minimal
    [{"assetId": …}] shape; the other five list keys go out as []."""
    existing = {
        "id": "evt-1", "name": "Test Event", "businessName": "Test Biz",
        "eventAssetsDtoList": [{"assetId": "KEV1", "assetName": "KEV1"}],
        "eventStaffsDtoList": [{"userId": "s1", "firstName": "Jane"}],
    }
    payload, result = _run_update(existing, ccAmount=100.0)

    assert payload["eventAssetsList"] == [{"assetId": "KEV1"}]
    for field in EMPTIED_FIELDS:
        assert payload[field] == [], f"{field} must be sent as []"
    assert payload["ccAmount"] == 100.0
    # Diagnostic counts + names reflect the real assignment (re-asserted, so
    # guaranteed still present after the write).
    assert result["_equipment_preserved"] == 1
    assert result["_equipment_names"] == ["KEV1"]
    assert result["_staff_preserved"] == 1
    assert result["_staff_names"] == ["Jane"]


def test_update_event_empty_lists_when_truly_unassigned():
    """No equipment on the event → eventAssetsList is [] (nothing to clear),
    and the other five are [] too."""
    existing = {"id": "evt-3", "name": "Bare Event", "businessName": "Biz"}
    payload, result = _run_update(existing, ccAmount=5.0)

    assert payload["eventAssetsList"] == []
    for field in EMPTIED_FIELDS:
        assert payload[field] == []
    assert result["_equipment_preserved"] == 0
    assert result["_staff_preserved"] == 0


def test_update_event_ignores_caller_asset_override_and_reasserts_real():
    """A caller can't set equipment through this path — we always re-assert the
    event's REAL current equipment, so a stray eventAssetsList kwarg can never
    reassign or wipe it."""
    existing = {
        "id": "evt-2", "name": "Test Event 2", "businessName": "Test Biz 2",
        "eventAssetsDtoList": [{"assetId": "KEV1", "assetName": "KEV1"}],
    }
    payload, _ = _run_update(existing, eventAssetsList=[{"assetId": "KEV9"}])

    assert payload["eventAssetsList"] == [{"assetId": "KEV1"}]


def test_update_event_refuses_empty_asset_list_when_equipment_present():
    """If the assetId re-assert comes back empty while the event clearly has
    equipment (shape drift), fail loudly rather than send [] and wipe it."""
    existing = {
        "id": "evt-4", "businessName": "Biz",
        "eventAssetsDtoList": [{"assetName": "KEV1"}],  # no assetId
    }
    with pytest.raises(RuntimeError, match="empty eventAssetsList"):
        _run_update(existing, ccAmount=1.0)
