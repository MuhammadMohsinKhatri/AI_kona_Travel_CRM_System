"""Token/cost capture for the AI reads behind Record Payments.

read_check and parse_cash_speech both go through _json_reply, which now
returns the API's own usage figures rather than discarding them — these tests
pin that the resulting CheckRead/SpeechRead carry real numbers, computed from
the vision model's own (pricier, different-from-the-classifier) rate, and that
a failed read costs nothing rather than lying with a zero that looks measured.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_konaice.db")
os.environ["CRM_PROVIDER"] = "mock"
os.environ["SQUARE_PROVIDER"] = "mock"
os.environ["OPENAI_PROVIDER"] = "mock"
os.environ["TELEGRAM_PROVIDER"] = "mock"

import json  # noqa: E402

from app.config import settings  # noqa: E402
from app.core import intake_readers  # noqa: E402


class _FakeUsage:
    def __init__(self, prompt_tokens, completion_tokens):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _FakeResponse:
    def __init__(self, payload: dict, prompt_tokens: int, completion_tokens: int):
        message = type("M", (), {"content": json.dumps(payload)})
        choice = type("C", (), {"message": message})
        self.choices = [choice]
        self.usage = _FakeUsage(prompt_tokens, completion_tokens)


def _fake_client(response: _FakeResponse):
    completions = type("Completions", (), {"create": lambda self, **kw: response})()
    chat = type("Chat", (), {"completions": completions})()
    return type("Client", (), {"chat": chat})()


def test_read_check_captures_usage_and_cost(monkeypatch):
    monkeypatch.setattr(settings, "openai_provider", "live")
    monkeypatch.setattr(settings, "openai_vision_input_cost_per_mtok", 2.5)
    monkeypatch.setattr(settings, "openai_vision_output_cost_per_mtok", 10.0)
    response = _FakeResponse(
        {"payer_name": "Acme PTA", "amount": 131.44, "confidence": "high"},
        prompt_tokens=1000, completion_tokens=200,
    )
    monkeypatch.setattr(intake_readers, "_client", lambda: _fake_client(response))

    check = intake_readers.read_check(b"fake-image-bytes", "image/jpeg")

    assert check.payer_name == "Acme PTA"
    assert check.ai_prompt_tokens == 1000
    assert check.ai_completion_tokens == 200
    # 1000/1e6*2.5 + 200/1e6*10.0 = 0.0025 + 0.002
    assert check.ai_cost_usd == 0.0045


def test_read_check_with_no_image_costs_nothing():
    """A read that never reached the API must show zero cost, not a made-up
    figure — there is nothing here for the API call's usage to overwrite."""
    check = intake_readers.read_check(b"", "image/jpeg")
    assert check.error
    assert check.ai_prompt_tokens == 0
    assert check.ai_cost_usd == 0.0


def test_read_check_api_failure_costs_nothing(monkeypatch):
    """An exception from the API arrives before any usage figure exists —
    the failed read must not be billed against in the UI."""
    monkeypatch.setattr(settings, "openai_provider", "live")

    def boom():
        raise RuntimeError("network is down")
    monkeypatch.setattr(intake_readers, "_client", boom)

    check = intake_readers.read_check(b"fake-image-bytes", "image/jpeg")
    assert "network is down" in check.error
    assert check.ai_cost_usd == 0.0


def test_parse_cash_speech_captures_usage(monkeypatch):
    monkeypatch.setattr(settings, "openai_provider", "live")
    monkeypatch.setattr(settings, "openai_vision_input_cost_per_mtok", 2.5)
    monkeypatch.setattr(settings, "openai_vision_output_cost_per_mtok", 10.0)
    response = _FakeResponse(
        {"entries": [{"query": "Pikesville", "amount": 7.0}], "notes": ""},
        prompt_tokens=500, completion_tokens=50,
    )
    monkeypatch.setattr(intake_readers, "_client", lambda: _fake_client(response))

    speech = intake_readers.parse_cash_speech("Pikesville took seven bucks")

    assert len(speech.entries) == 1
    assert speech.ai_prompt_tokens == 500
    assert speech.ai_completion_tokens == 50
    # 500/1e6*2.5 + 50/1e6*10.0 = 0.00125 + 0.0005
    assert speech.ai_cost_usd == 0.00175
