"""Finding which event a spoken phrase or a check belongs to.

Both intake flows need the same thing: someone says "Pikesville farmers market
took $7" or a check arrives from "JONES ELEMENTARY SCHOOL PTA", and we have to
decide which KonaOS event that is before touching any money.

The n8n workflows this replaces asked GPT-4o to do it, handing the model a
scoring table ("+30 brand match, −30 brand conflict, +50 exact name…") and
trusting it to add up. That is arithmetic, and a computer should do arithmetic:
ported here it is deterministic, reproducible run to run, free, instant, and —
most importantly — testable. The same phrase can no longer match one event today
and a different one tomorrow, which for something that marks invoices paid is
the difference between a tool and a liability.

The scoring weights are the workflow's, kept verbatim so behaviour carries over.

The one thing this deliberately does NOT do is guess. Ambiguity returns no match
and says why, because the cost of a wrong match here is a payment recorded
against the wrong customer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

# ── brands ───────────────────────────────────────────────────────────────────
# Canonical names, and the spoken/typed forms people actually use. Callers say
# "kona", "tt", "travelin toms"; KonaOS says "Kona Ice" / "Travelin' Tom's".
KONA = "Kona Ice"
TOMS = "Travelin' Tom's"
BEVERLY = "Beverly Ann's"

BRAND_IDS = {
    KONA: "66704154faed4c5991533eb5253815d9",
    TOMS: "4553cb46d02d40e4ab2732673e141ac3",
    BEVERLY: "ffc8d7f43fd64dbb99e97c9aa42e96aa",
}

_BRAND_ALIASES = {
    KONA: ("kona ice", "konaice", "kona", "ki"),
    TOMS: ("travelin' tom's", "travelin toms", "travelintoms", "travelin tom",
           "travelintom", "tom's", "toms", "tt"),
    BEVERLY: ("beverly ann's", "beverly anns", "beverlyanns", "beverly"),
}

# Which equipment belongs to which brand. A truck named KEV3 on an event the
# caller says is Travelin' Tom's is a contradiction worth points against.
_BRAND_ASSETS = {
    KONA: lambda a: a.startswith("kev") or a in ("kiosk1", "kiosk2", "mini"),
    TOMS: lambda a: "bev1" in a or "tom's table" in a or "toms table" in a,
}

# Words that carry no identifying signal in an event or business name.
_STOP_WORDS = {
    "the", "and", "of", "for", "to", "in", "at", "by", "with", "from", "on",
    "an", "a", "event", "pta", "inc", "llc", "school", "center", "centre",
}

# Short forms people say out loud. Expanded before matching so "bv" reaches
# "brightview" — the workflow's own examples.
_EXPANSIONS = {
    "bv": "brightview",
    "milford": "milford mill",
    "es": "elementary",
    "ms": "middle",
    "hs": "high",
}

_ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
_NON_WORD_RE = re.compile(r"[^\w']+")

# Two candidates this close together are a coin flip, not a match.
AMBIGUITY_MARGIN = 10


def canonical_brand(raw: Any) -> str:
    """Canonical brand name for whatever the caller said, or "" if unknown.

    "" is the neutral value throughout: brand then scores nothing either way
    rather than acting as a filter, because most spoken input omits it.
    """
    text = str(raw or "").strip().lower().replace("’", "'")
    if not text:
        return ""
    for brand, aliases in _BRAND_ALIASES.items():
        if text in aliases or text == brand.lower():
            return brand
    for brand, aliases in _BRAND_ALIASES.items():
        if any(a in text for a in aliases):
            return brand
    return ""


def _tokens(text: str) -> list[str]:
    words = [w for w in _NON_WORD_RE.split(str(text or "").lower()) if w]
    out: list[str] = []
    for w in words:
        out.extend(_EXPANSIONS.get(w, w).split())
    return out


def _significant(tokens: list[str]) -> list[str]:
    """Tokens worth matching on. Falls back to everything when stripping stop
    words would leave nothing — "The Center" is all stop words but is still the
    only thing the caller said."""
    kept = [t for t in tokens if t not in _STOP_WORDS and len(t) > 1]
    return kept or tokens


def _name_score(query: str, event_name: str) -> tuple[int, str]:
    """How much the query looks like this event's name. The primary signal."""
    q, n = str(query or "").lower().strip(), str(event_name or "").lower().strip()
    if not q or not n:
        return 0, "name+0"
    if q == n:
        return 50, "name+50 exact"

    q_tokens = _significant(_tokens(q))
    n_tokens = _tokens(n)
    if not q_tokens:
        return 0, "name+0"

    hits = sum(1 for t in q_tokens if any(t == nt or t in nt for nt in n_tokens))
    if hits == len(q_tokens):
        return 40, "name+40 all tokens"
    if hits / len(q_tokens) > 0.5:
        return 25, "name+25 most tokens"

    # A shared opening ("brightvue" vs "brightview") or one short word that is
    # simply inside the name ("kona" inside "Kona Ice Social").
    for t in q_tokens:
        for nt in n_tokens:
            if len(t) >= 4 and len(nt) >= 4 and t[:4] == nt[:4]:
                return 15, "name~15 prefix match"
        if len(t) <= 5 and t in n:
            return 15, "name~15 short token inside name"
    return 0, "name+0"


def _location_score(query: str, event: dict[str, Any]) -> tuple[int, str]:
    """A zip or city named in the query, confirming the name match."""
    q = str(query or "").lower()
    zips = {z for z in _ZIP_RE.findall(q)}
    event_zip = str(event.get("zipCode") or "").strip()[:5]
    if event_zip and event_zip in zips:
        return 20, "location+20 zip"

    city = str(event.get("city") or "").strip().lower()
    if city and city in q:
        return 20, "location+20 city"

    street = str(event.get("addressLine1") or "").strip().lower()
    if street:
        street_tokens = [t for t in _tokens(street) if len(t) > 3 and not t.isdigit()]
        if any(t in q for t in street_tokens):
            return 10, "location+10 street"
    return 0, "location+0"


def _asset_score(brand: str, event: dict[str, Any]) -> tuple[int, str]:
    """Equipment as brand confirmation. Only meaningful when a brand was given,
    and never a hard filter — plenty of events carry no equipment at all."""
    if not brand or brand not in _BRAND_ASSETS:
        return 0, "asset~skipped"
    names = event.get("assetNames") or event.get("assetIds") or ""
    if isinstance(names, (list, tuple)):
        names = ", ".join(str(n) for n in names)
    assets = [a.strip().lower() for a in str(names).split(",") if a.strip()]
    if not assets:
        return 0, "asset~none recorded"
    matches = _BRAND_ASSETS[brand]
    if any(matches(a) for a in assets):
        return 10, "asset+10"
    # Every asset belongs to a different brand — that contradicts the caller.
    if any(fn(a) for a in assets for b, fn in _BRAND_ASSETS.items() if b != brand):
        return -20, "asset⚠ mismatch −20"
    return 0, "asset+0"


def _event_brand(event: dict[str, Any]) -> str:
    """The event's brand, from its own field or inferred from its equipment.

    The events grid does not return brandName, so equipment is usually the only
    signal available — which is exactly why an asset mismatch scores against a
    candidate rather than being ignored.
    """
    explicit = canonical_brand(event.get("brandName") or event.get("brand"))
    if explicit:
        return explicit
    names = event.get("assetNames") or ""
    if isinstance(names, (list, tuple)):
        names = ", ".join(str(n) for n in names)
    assets = [a.strip().lower() for a in str(names).split(",") if a.strip()]
    for brand, matches in _BRAND_ASSETS.items():
        if any(matches(a) for a in assets):
            return brand
    return ""


@dataclass
class Candidate:
    """One event scored against the query, with its reasoning kept."""

    event: dict[str, Any]
    score: int
    name_score: int
    flags: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return str(self.event.get("id") or "")

    @property
    def name(self) -> str:
        return str(self.event.get("name") or "")


@dataclass
class MatchResult:
    """What the matcher decided, and why.

    ``event`` is None whenever a human needs to choose — no match, or a tie the
    matcher refuses to break. ``candidates`` is always populated so the UI can
    show the near-misses instead of a bare "not found".
    """

    event: Optional[dict[str, Any]]
    reason: str
    candidates: list[Candidate] = field(default_factory=list)
    needs_choice: bool = False

    @property
    def event_id(self) -> str:
        return str((self.event or {}).get("id") or "")


def match_event(
    events: list[dict[str, Any]],
    query: str,
    brand: Any = "",
) -> MatchResult:
    """Pick the event a phrase refers to, out of the events for that day.

    Callers pass events already filtered to the relevant date — the date is a
    hard filter in the original workflow, and doing it in the query rather than
    the scoring keeps this function about identity alone.
    """
    wanted_brand = canonical_brand(brand)
    candidates: list[Candidate] = []

    for event in events:
        flags: list[str] = []
        score = 0

        name_pts, name_flag = _name_score(query, event.get("name", ""))
        score += name_pts
        flags.append(name_flag)

        event_brand = _event_brand(event)
        if wanted_brand and event_brand:
            if event_brand == wanted_brand:
                score += 30
                flags.append("brand+30")
            else:
                score -= 30
                flags.append(f"brand⚠ conflict −30 (event is {event_brand})")
        else:
            flags.append("brand~neutral")

        loc_pts, loc_flag = _location_score(query, event)
        score += loc_pts
        flags.append(loc_flag)

        asset_pts, asset_flag = _asset_score(wanted_brand, event)
        score += asset_pts
        flags.append(asset_flag)

        candidates.append(Candidate(event=event, score=score,
                                    name_score=name_pts, flags=flags))

    candidates.sort(key=lambda c: (c.score, c.name_score), reverse=True)

    if not candidates:
        return MatchResult(None, "No events on that date to match against.", [])

    scored = [c for c in candidates if c.score > 0]
    if not scored:
        return MatchResult(
            None,
            "Nothing on that date resembles that name. Check the date, or say "
            "more of the event name.",
            candidates[:5],
        )

    best = scored[0]

    # Name similarity leads. A candidate that wins only because its brand
    # matched, while the closest name match sits on a brand conflict, is the
    # workflow's documented failure mode — it silently billed the wrong event.
    strongest_name = max(candidates, key=lambda c: c.name_score)
    if (strongest_name.name_score > best.name_score
            and strongest_name.score <= 0
            and best.name_score <= 15):
        return MatchResult(
            None,
            f"Closest name match is \"{strongest_name.name}\" but its brand "
            f"doesn't match. Confirm the brand, or name the event more exactly.",
            candidates[:5],
            needs_choice=True,
        )

    runner_up = scored[1] if len(scored) > 1 else None
    if runner_up and best.score - runner_up.score < AMBIGUITY_MARGIN:
        same_brand = _event_brand(best.event) == _event_brand(runner_up.event)
        detail = ("Say which brand." if not same_brand
                  else "Add the town or zip code.")
        return MatchResult(
            None,
            f"\"{best.name}\" and \"{runner_up.name}\" match about equally "
            f"well. {detail}",
            candidates[:5],
            needs_choice=True,
        )

    return MatchResult(
        best.event,
        f"Matched \"{best.name}\" ({', '.join(best.flags)}).",
        candidates[:5],
    )
