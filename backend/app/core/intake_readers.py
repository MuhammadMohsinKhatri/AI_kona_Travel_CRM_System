"""Turning a photo of a check, or a spoken sentence, into structured intake.

The genuinely-AI half of the intake flows. Everything after this point —
matching the event, computing the settlement, writing to KonaOS — is ordinary
code (see event_matcher, intake_service), because it either has to be testable
or has to be right every time.

Two jobs:

  * ``read_check`` — a photo of a paper check to payer name, address, amount and
    date. Vision, no way around it.
  * ``parse_cash_speech`` — one dictated sentence covering several events to a
    list of (which event, how much). Also genuinely language work: "Pikesville
    took seven bucks, Camp Lollipop was twelve fifty" has no grammar to parse.

Both return best-effort structure with the raw input kept alongside, and NEITHER
decides anything. A misread amount that goes straight to KonaOS is a wrong
payment; a misread amount shown on a review screen is a typo someone fixes in
two seconds. The review step is the whole safety model, so these functions are
free to be as fallible as models are.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from app.config import settings

# Vision and audio need a multimodal model; the classifier's gpt-5-mini is
# text-only. Overridable so a model change doesn't need a code change.
VISION_MODEL = "gpt-4o"
TRANSCRIBE_MODEL = "whisper-1"

_CHECK_PROMPT = """You read US paper checks and return JSON.

Return exactly:
{
  "payer_name": "the name printed TOP-LEFT — the account holder paying us",
  "payer_address": "the address printed under that name, one line, or \\"\\"",
  "amount": 408.10,
  "check_date": "YYYY-MM-DD or \\"\\" if unreadable",
  "check_number": "the number top-right, or \\"\\"",
  "memo": "the memo line, or \\"\\"",
  "confidence": "high | medium | low",
  "notes": "anything ambiguous, smudged or contradictory — one short sentence"
}

Rules that matter:
- The image may be a phone snap at an angle, a flatbed scan, a fax, a screenshot
  of one, or a check sitting on a desk among other paper. All of those are
  checks: read them. Say it is not a check ONLY when no check is present at all.
- payer_name is the PAYER (top-left), never the payee ("Kona Ice", "Travelin'
  Tom's", "Beverly Ann's"). Getting this backwards makes the check unmatchable.
  It is usually an organisation — a school, a PTA, a company, an HOA — printed
  above an address block. If that block is cut off or smudged, take the account
  holder's name from the address block or the pre-printed signature line, and
  say in notes where you got it. Do not take it from handwriting alone.
- amount: prefer the numeric box; if the written words disagree with the box,
  report the WRITTEN words as the amount and say so in notes — the written
  amount is legally controlling.
- Report every field you CAN read even when others defeat you. A check with a
  clear amount and an unreadable payer is far more useful than an empty answer:
  the amount alone often identifies the invoice.
- Never invent. Anything you cannot read is "" and confidence drops.
- Return only the JSON object.
"""

_SPEECH_PROMPT = """You turn a dictated sentence about cash collected at events
into a JSON list. The speaker is an office admin reading off takings, often for
several events at once.

TODAY IS {today}. Every date you return must be resolved against that.

Return exactly:
{{
  "entries": [
    {{"query": "what identifies the event, in their words", "amount": 7.00,
     "brand": "kona ice | travelin toms | \\"\\"", "date": "YYYY-MM-DD or \\"\\""}}
  ],
  "notes": "anything you could not confidently split — one short sentence"
}}

Rules that matter:
- One entry per event mentioned. "Pikesville took seven, Lollipop was twelve" is
  two entries.
- amount is a number of dollars. "seven bucks" -> 7.0, "twelve fifty" -> 12.50,
  "two hundred" -> 200.0.
- query keeps their wording, including any town or zip they said — that is what
  identifies the event downstream. Do not tidy it into something shorter. Leave
  the date out of query if you resolved it into the date field.
- date: resolve what they said against TODAY. "29th July" or "the 29th" means the
  most recent 29th July that is NOT in the future. "yesterday" and "Tuesday" mean
  exactly that, counting back from today. Events are being reported AFTER they
  happened, so never resolve a date into the future — step back a year rather
  than forward. If they named no date at all, return "" and do not guess one: an
  invented date searches a day with no events on it and finds nothing.
- brand only if actually said.
- An amount with no identifiable event still gets an entry with query "" so a
  person sees it was heard and can say which event it was.
- Return only the JSON object.
"""


@dataclass
class CheckRead:
    """What we think the check says. Every field is a suggestion for review."""

    payer_name: str = ""
    payer_address: str = ""
    amount: float = 0.0
    check_date: str = ""
    check_number: str = ""
    memo: str = ""
    confidence: str = "low"
    notes: str = ""
    error: str = ""

    @property
    def usable(self) -> bool:
        """Enough to attempt a match: somebody to match on, and a figure."""
        return bool(self.payer_name.strip()) and self.amount > 0


@dataclass
class CashEntry:
    """One event's takings as heard."""

    query: str = ""
    amount: float = 0.0
    brand: str = ""
    date: str = ""


@dataclass
class SpeechRead:
    entries: list[CashEntry] = field(default_factory=list)
    transcript: str = ""
    notes: str = ""
    error: str = ""


def _client():
    from openai import OpenAI

    from app.integrations.live import _clean_api_key

    return OpenAI(api_key=_clean_api_key(settings.openai_api_key))


def _num(v: Any) -> float:
    try:
        n = float(str(v).replace(",", "").replace("$", "").strip() or 0)
        return 0.0 if n != n else n
    except (TypeError, ValueError):
        return 0.0


def _json_reply(model: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    resp = _client().chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=messages,
    )
    return json.loads(resp.choices[0].message.content or "{}")


def read_check(image_bytes: bytes, content_type: str = "image/jpeg") -> CheckRead:
    """Read a photo of a check.

    Errors come back on the object rather than raised: a failed read must show
    the user "couldn't read it, type the details" instead of a 500 on an upload
    they cannot retry any differently.
    """
    if not image_bytes:
        return CheckRead(error="No image was uploaded.")
    if settings.openai_provider != "live":
        return CheckRead(error="Check reading needs OPENAI_PROVIDER=live.")

    b64 = base64.b64encode(image_bytes).decode("ascii")
    try:
        data = _json_reply(VISION_MODEL, [
            {"role": "system", "content": _CHECK_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": "Read this check."},
                {"type": "image_url",
                 "image_url": {"url": f"data:{content_type};base64,{b64}"}},
            ]},
        ])
    except Exception as e:  # noqa: BLE001 — surface, never crash the upload
        return CheckRead(error=f"Couldn't read the check: {e}")

    return CheckRead(
        payer_name=str(data.get("payer_name") or "").strip(),
        payer_address=str(data.get("payer_address") or "").strip(),
        amount=_num(data.get("amount")),
        check_date=str(data.get("check_date") or "").strip(),
        check_number=str(data.get("check_number") or "").strip(),
        memo=str(data.get("memo") or "").strip(),
        confidence=str(data.get("confidence") or "low").strip().lower(),
        notes=str(data.get("notes") or "").strip(),
    )


def transcribe(audio_bytes: bytes, filename: str = "speech.webm") -> tuple[str, str]:
    """Audio to text. Returns (transcript, error)."""
    if not audio_bytes:
        return "", "No audio was recorded."
    if settings.openai_provider != "live":
        return "", "Voice input needs OPENAI_PROVIDER=live."
    try:
        resp = _client().audio.transcriptions.create(
            model=TRANSCRIBE_MODEL,
            file=(filename, audio_bytes),
            # Business names and dollar amounts are what this has to get right,
            # and both are exactly what a general model mangles.
            prompt="Event names, towns, zip codes and dollar amounts for a "
                   "shaved-ice catering business (Kona Ice, Travelin' Tom's).",
        )
        return str(getattr(resp, "text", "") or "").strip(), ""
    except Exception as e:  # noqa: BLE001
        return "", f"Couldn't transcribe the recording: {e}"


def parse_cash_speech(transcript: str, today: str = "") -> SpeechRead:
    """Split a dictated sentence into one entry per event mentioned.

    ``today`` (YYYY-MM-DD) is what "29th July" or "yesterday" get resolved
    against. Without it the model has no year to work from and quietly invents
    one — usually from its training data — which then searches a day with no
    events on it and reports that nothing matched. The date is the one part of
    this a model cannot infer from the sentence, so it has to be supplied.
    """
    text = str(transcript or "").strip()
    if not text:
        return SpeechRead(error="Nothing was said.")
    if settings.openai_provider != "live":
        return SpeechRead(transcript=text,
                          error="Voice input needs OPENAI_PROVIDER=live.")
    prompt = _SPEECH_PROMPT.format(today=today or date.today().isoformat())
    try:
        data = _json_reply(VISION_MODEL, [
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ])
    except Exception as e:  # noqa: BLE001
        return SpeechRead(transcript=text, error=f"Couldn't read that back: {e}")

    entries = [
        CashEntry(
            query=str(row.get("query") or "").strip(),
            amount=_num(row.get("amount")),
            brand=str(row.get("brand") or "").strip(),
            date=str(row.get("date") or "").strip(),
        )
        for row in (data.get("entries") or [])
        if isinstance(row, dict)
    ]
    return SpeechRead(entries=entries, transcript=text,
                      notes=str(data.get("notes") or "").strip())
