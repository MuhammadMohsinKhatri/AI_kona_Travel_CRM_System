"""Matching a spoken phrase or a check to the right KonaOS event.

This is the safety-critical piece of both intake flows: everything downstream
(cash written onto an event, an invoice marked paid, a 4% fee waived) happens to
whichever event comes out of here. A wrong match records someone else's payment.

The n8n workflows asked GPT-4o to add up a scoring table. Porting the arithmetic
to code is what makes these tests possible at all — the same phrase now resolves
the same way every time.
"""
import os

os.environ.setdefault("CRM_PROVIDER", "mock")

from app.core.event_matcher import (KONA, TOMS, canonical_brand, match_event)


def _event(id, name, **kw):
    return {"id": id, "name": name, **kw}


PIKESVILLE = _event("e-pike", "(IC) Pikesville Farmers Market",
                    city="Pikesville", state="Maryland", zipCode="21208",
                    assetNames="KEV1")
LOLLIPOP = _event("e-lolli", "(SK) Camp Lollipop", city="Baltimore",
                  zipCode="21133", assetNames="KIOSK2")
MANHEIM = _event("e-man", "(B) Manheim Auto Auction", city="Bowie",
                 zipCode="20716", assetNames="BEV1")
WALMART = _event("e-wal", "Walmart", city="Severna Park", zipCode="21146",
                 assetNames="KEV5")

DAY = [PIKESVILLE, LOLLIPOP, MANHEIM, WALMART]


# ── brand vocabulary ─────────────────────────────────────────────────────────

def test_spoken_brand_forms_resolve():
    for said in ("kona", "Kona Ice", "konaice", "KI"):
        assert canonical_brand(said) == KONA, said
    for said in ("tt", "travelin toms", "travelintoms", "Travelin' Tom's"):
        assert canonical_brand(said) == TOMS, said


def test_an_unspecified_brand_is_neutral_not_a_filter():
    """Most spoken input omits the brand. Treating "" as a filter would match
    nothing at all."""
    assert canonical_brand("") == ""
    assert canonical_brand("something else") == ""
    assert match_event(DAY, "pikesville farmers market", "").event_id == "e-pike"


# ── the ordinary case ────────────────────────────────────────────────────────

def test_matches_on_name():
    assert match_event(DAY, "pikesville farmers market").event_id == "e-pike"


def test_matches_a_partial_name():
    assert match_event(DAY, "camp lollipop").event_id == "e-lolli"


def test_matches_with_a_zip_code_spoken_aloud():
    """The workflow's own example shape: "sports event that happened in 21208"."""
    assert match_event(DAY, "farmers market in 21208").event_id == "e-pike"


def test_matches_on_town():
    assert match_event(DAY, "the walmart one in severna park").event_id == "e-wal"


def test_tolerates_a_misspelling():
    """Voice transcription mangles names; a shared opening still resolves."""
    assert match_event(DAY, "pikesvile farmer market").event_id == "e-pike"


def test_brand_confirms_the_match():
    r = match_event(DAY, "manheim auto auction", "travelin toms")
    assert r.event_id == "e-man"
    assert any("brand+30" in f for f in r.candidates[0].flags)


# ── refusing to guess ────────────────────────────────────────────────────────

def test_no_resemblance_is_not_a_match():
    r = match_event(DAY, "totally unrelated birthday party")
    assert r.event is None
    assert not r.event_id
    assert "resembles" in r.reason


def test_an_empty_day_is_not_a_match():
    r = match_event([], "pikesville")
    assert r.event is None
    assert "No events on that date" in r.reason


def test_two_equally_good_matches_ask_instead_of_picking():
    """A coin flip between two events must not be resolved silently — one of
    them is somebody else's payment."""
    twins = [
        _event("a", "Riverside Park", city="Bowie", zipCode="20716", assetNames="KEV1"),
        _event("b", "Riverside Park", city="Bowie", zipCode="20716", assetNames="KEV2"),
    ]
    r = match_event(twins, "riverside park")
    assert r.event is None
    assert r.needs_choice
    assert "match about equally well" in r.reason


def test_a_brand_tie_asks_which_brand():
    twins = [
        _event("a", "Riverside Park", city="Bowie", assetNames="KEV1"),
        _event("b", "Riverside Park", city="Bowie", assetNames="BEV1"),
    ]
    r = match_event(twins, "riverside park")
    assert r.event is None
    assert "Say which brand" in r.reason


def test_a_brand_conflict_is_surfaced_not_overridden():
    """The workflow's documented failure: the closest name match has the wrong
    brand, and it silently fell through to a brand-matched event that didn't
    resemble the query at all."""
    events = [
        _event("named", "Jones Elementary PTA", city="Bowie", assetNames="BEV1"),
        _event("branded", "Completely Different Thing", city="Bowie", assetNames="KEV1"),
    ]
    r = match_event(events, "jones elementary pta", "kona")
    assert r.event is None, "must not fall through to the brand-matched event"
    assert "brand" in r.reason.lower()


def test_a_strong_name_match_survives_a_brand_conflict_being_reported():
    """Flip side: when the name match is unambiguous it still wins, because the
    caller is more likely to have misremembered the brand than the name."""
    events = [
        _event("named", "Pikesville Farmers Market", city="Pikesville",
               zipCode="21208", assetNames="KEV1"),
        _event("other", "Nothing Alike", city="Bowie", assetNames="BEV1"),
    ]
    r = match_event(events, "pikesville farmers market 21208", "kona")
    assert r.event_id == "named"


# ── reasoning is always available ────────────────────────────────────────────

def test_candidates_are_returned_even_when_nothing_matched():
    """A bare "not found" is unactionable. The near-misses are what let someone
    see the date was wrong, or pick from a list."""
    r = match_event(DAY, "totally unrelated birthday party")
    assert r.candidates
    assert all(c.flags for c in r.candidates)


def test_the_winner_explains_itself():
    r = match_event(DAY, "pikesville farmers market in 21208", "kona")
    assert "Matched" in r.reason
    flags = r.candidates[0].flags
    assert any("name+" in f for f in flags)
    assert any("location+20" in f for f in flags)
    assert any("asset+10" in f for f in flags)


def test_equipment_from_the_wrong_brand_scores_against_a_candidate():
    events = [_event("x", "Riverside Park", assetNames="BEV1")]
    r = match_event(events, "riverside park", "kona")
    assert any("asset⚠ mismatch" in f for f in r.candidates[0].flags)


def test_an_event_with_no_equipment_is_still_matchable():
    """Plenty of events carry no assets; treating that as a brand contradiction
    would make them permanently unmatchable."""
    events = [_event("x", "Pikesville Farmers Market", city="Pikesville")]
    r = match_event(events, "pikesville farmers market", "kona")
    assert r.event_id == "x"
