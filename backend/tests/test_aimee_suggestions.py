"""The empty-state chips.

The empty state exists so the FIRST interaction succeeds — nobody knows what an
assistant can do until they have failed at it twice. A chip for "where are the
trucks" on an install with no Samsara token defeats that exactly: it advertises
a capability, and the first thing a new user clicks answers "I can't reach the
trucks right now".
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_konaice.db")
os.environ["CRM_PROVIDER"] = "mock"
os.environ["SQUARE_PROVIDER"] = "mock"
os.environ["OPENAI_PROVIDER"] = "mock"
os.environ["TELEGRAM_PROVIDER"] = "mock"

import pytest  # noqa: E402

from app.api.routes.aimee import _suggestions  # noqa: E402
from app.config import settings  # noqa: E402


@pytest.fixture
def nothing(monkeypatch):
    for key in ("samsara_api_token", "google_maps_api_key",
                "square_kona_token", "square_tom_token"):
        monkeypatch.setattr(settings, key, "")


@pytest.fixture
def everything(monkeypatch):
    monkeypatch.setattr(settings, "samsara_api_token", "t")
    monkeypatch.setattr(settings, "google_maps_api_key", "k")
    monkeypatch.setattr(settings, "square_kona_token", "s")


def test_unconfigured_integrations_offer_no_chips(nothing):
    labels = [s["label"] for s in _suggestions()]
    assert "Where are the trucks?" not in labels
    assert "Street view" not in labels
    assert "Who's clocked in?" not in labels
    # ...but the ones that need no credential are still there.
    assert "What's on this week?" in labels


def test_configuring_them_adds_their_chips(everything):
    labels = [s["label"] for s in _suggestions()]
    for expected in ("Where are the trucks?", "Anything need fuel?",
                     "Street view", "Who's clocked in?"):
        assert expected in labels


def test_truck_eta_needs_both_samsara_and_maps(monkeypatch):
    """It reads a truck's live position AND asks Google for the drive. One
    without the other is a chip that cannot work."""
    monkeypatch.setattr(settings, "samsara_api_token", "t")
    monkeypatch.setattr(settings, "google_maps_api_key", "")
    assert "Truck ETA" not in [s["label"] for s in _suggestions()]

    monkeypatch.setattr(settings, "google_maps_api_key", "k")
    assert "Truck ETA" in [s["label"] for s in _suggestions()]


def test_incomplete_chips_end_in_a_space(everything):
    """The trailing space is the signal the chat reads to fill the box instead
    of sending. Without it, "Record $60 cash for " goes out as a question with
    no event and Aimee has to reply that the message was cut off."""
    by_label = {s["label"]: s["text"] for s in _suggestions()}
    for needs_finishing in ("Record cash", "Truck ETA", "Plan a route", "Street view"):
        assert by_label[needs_finishing].endswith(" "), needs_finishing
    for complete in ("What's on this week?", "Anything need fuel?", "Top clients"):
        assert not by_label[complete].endswith(" "), complete


def test_every_chip_is_labelled_and_grouped(everything):
    for s in _suggestions():
        assert s["label"] and s["icon"] and s["text"]
        assert s["group"], f"{s['label']} has no group to render under"
