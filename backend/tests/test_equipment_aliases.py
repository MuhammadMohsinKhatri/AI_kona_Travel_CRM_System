"""Device-name aliases.

Drivers write device names the way they say them, not the way the CRM spells
them. An unresolved name means device_id=None, which makes the Square search
return nothing — and "no Square sales" is not a neutral outcome: on a
minimum-guarantee event it bills the host the FULL minimum instead of the
shortfall.
"""
import pytest

from app.core.equipment import MAPPED_DEVICE_IDS, alias_key, map_equipment

MINI = MAPPED_DEVICE_IDS["MINI"]


def _driver_used(name: str) -> dict:
    return map_equipment({
        "SQUARE_USED": "TRUE",
        "ASSIGNED_EQUIPMENT": "KEV6 (SK)",
        "DRIVER_REPORTED_EQUIPMENT": name,
    })


def test_terminal_m_resolves_to_the_mini_terminal():
    """The reported case: Gallery Tower, driver wrote "Used terminal M". Neither
    "TERMINAL M" nor its first word "TERMINAL" is a key, so this resolved to no
    device and the event reconciled 0 orders / $0.00."""
    result = _driver_used("TERMINAL M")
    assert result["device_id"] == MINI
    assert result["status"] == "Match Found"
    assert "Alias" in result["matched_via"]


@pytest.mark.parametrize("written", [
    "TERMINAL M", "terminal m", "M Terminal", "Mini Terminal", "mini",
    "Terminal Mini", "Square Mini", "used the mini terminal",
])
def test_mini_terminal_spellings(written):
    assert _driver_used(written)["device_id"] == MINI


def test_kev_wins_over_the_word_terminal():
    """"KEV6 terminal" must stay KEV6 — it must not fall through to the Mini
    rules just because it contains the word "terminal"."""
    assert _driver_used("KEV6 terminal")["device_id"] == MAPPED_DEVICE_IDS["KEV6"]
    assert _driver_used("KEV 6")["device_id"] == MAPPED_DEVICE_IDS["KEV6"]
    assert alias_key("KEV6 terminal") == "KEV6"


def test_kiosk_with_a_space():
    assert _driver_used("kiosk 2")["device_id"] == MAPPED_DEVICE_IDS["KIOSK2"]


def test_exact_names_are_untouched_by_the_alias_layer():
    """Aliases are consulted only after an exact lookup fails, so they can never
    change how an already-recognised name resolves."""
    for name in ("KEV6 (SK)", "KEV1 (SM)", "KIOSK2 (SK)", "MINI"):
        assert _driver_used(name)["device_id"] == MAPPED_DEVICE_IDS[name]


def test_unknown_equipment_still_reports_no_match():
    """The alias layer must not start guessing — an unrecognised name has to stay
    unmapped so it surfaces rather than silently attaching wrong sales."""
    result = _driver_used("the blue truck")
    assert result["device_id"] is None
    assert result["status"] == "No Device ID Mapped"
    assert alias_key("the blue truck") == ""


def test_assigned_equipment_is_used_when_square_was_not():
    result = map_equipment({
        "SQUARE_USED": "FALSE",
        "ASSIGNED_EQUIPMENT": "KEV6 (SK)",
        "DRIVER_REPORTED_EQUIPMENT": "TERMINAL M",
    })
    assert result["device_id"] == MAPPED_DEVICE_IDS["KEV6"]
    assert result["equipment_source"] == "Assigned"


def test_mismatch_is_still_flagged():
    """Gallery Tower reported KEV6 assigned vs TERMINAL M used. Resolving the
    alias must not hide that they differ."""
    assert _driver_used("TERMINAL M")["equipment_mismatch"] is True
