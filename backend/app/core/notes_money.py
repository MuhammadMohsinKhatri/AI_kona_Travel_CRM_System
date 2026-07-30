"""Reading dollar figures out of free-text CRM notes.

Two callers need the same primitives, and duplicating the regexes between them
would guarantee they drift:

  * ``invariants`` — the pre-invoice gate, which flags a stated figure that does
    no work in the billing (and must NOT flag one the notes themselves waive).
  * ``pipeline`` — which records the waived figures on the classification so the
    subtotal breakdown can show "Destination fee (waived) = $0.00" instead of
    leaving the reader to wonder where the $50 in the notes went.

Admin notes are written as slash-delimited fragments — "$4 12oz Kona / waived
$50 destination fee" — and the clause is the unit that decides what a waiver
applies to. That matters: a fixed character window around the figure puts
"waived" within 30 characters of the $99 in "waived $50 destination fee / $99
setup fee", which would silence a genuinely dropped base fee.
"""
from __future__ import annotations

import re
from typing import Any

AMOUNT_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)")

# Clause delimiters. Commas are NOT delimiters — they appear inside figures
# ("$1,200"). A period only splits when followed by whitespace or end of string,
# for the same reason ("$50.00").
CLAUSE_SPLIT_RE = re.compile(r"[/;\n]|\.(?=\s|$)")

# A fee the notes explicitly cancel. Deliberately narrow: "removed", "dropped"
# and "discount" all appear in notes that mean something else, and a loose
# pattern here silences the very check that catches a dropped base fee.
WAIVER_PATTERNS = (
    r"waiv\w*",            # waive / waived / waiving / waiver
    r"comp(?:ed|'d|ped)\b",
    r"no charge", r"free of charge", r"at no cost", r"not charging",
)

# Words that carry no meaning in a fee name once the waiver verb and the amount
# are stripped out ("waived the $50 destination fee" -> "destination fee").
_FILLER = {"the", "a", "an", "was", "is", "been", "be", "and", "of", "for",
           "her", "his", "their", "our", "this", "that", "we", "i", "will",
           "have", "has", "had", "per", "on", "in", "to"}

_TOL = 0.02


def _to_float(raw: Any) -> float:
    try:
        n = float(str(raw).replace(",", "").lstrip("$"))
        return 0.0 if n != n else n
    except (TypeError, ValueError):
        return 0.0


def clause_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges of the note's separate clauses."""
    spans, start = [], 0
    for m in CLAUSE_SPLIT_RE.finditer(text):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(text)))
    return spans


def clause_at(text: str, position: int) -> str:
    """The clause containing this character position."""
    for start, end in clause_spans(text):
        if start <= position < end:
            return text[start:end]
    return ""


def is_waived(text: str, position: int) -> bool:
    """Do the notes cancel the figure at this position?

    True when the figure's own clause also carries waiver language, in either
    order — "waived $50 destination fee" and "$50 destination fee was waived"
    read the same way to a person.
    """
    clause = clause_at(text, position).lower()
    return any(re.search(p, clause) for p in WAIVER_PATTERNS)


def fee_name(clause: str) -> str:
    """A readable name for the fee a clause is about, or "" if it has none.

    Built by removing what we already know — the waiver verb and the amount —
    and dropping filler, rather than by pattern-matching known fee names: the
    office invents wording ("destination", "travel", "setup", "cleaning") and a
    fixed list would quietly fall back to a generic label on anything new.
    """
    text = clause
    for pattern in WAIVER_PATTERNS:
        text = re.sub(pattern, " ", text, flags=re.I)
    text = AMOUNT_RE.sub(" ", text)
    words = [w for w in re.split(r"[^\w'’%-]+", text) if w]
    kept = [w for w in words if w.lower() not in _FILLER and not w.isdigit()]
    return " ".join(kept).strip()


def stated_amounts(text: str, include_waived: bool = False) -> list[float]:
    """Distinct dollar figures the notes state.

    Waived figures are excluded by default: a waived fee is one that SHOULD do
    no work in the billing, so flagging it asks a person to re-confirm a decision
    the notes already record.

    A figure counts as waived only if EVERY mention of it is waived — "waived the
    $50 destination fee" alongside a separate "$50 cleaning fee" must still be
    flagged, because one of the two really was dropped.
    """
    occurrences = [
        (_to_float(m.group(1)), is_waived(text, m.start()))
        for m in AMOUNT_RE.finditer(text)
        if _to_float(m.group(1)) > 0
    ]

    seen: list[float] = []
    for amount, _ in occurrences:
        if any(abs(amount - s) <= _TOL for s in seen):
            continue
        if not include_waived and all(
            w for a, w in occurrences if abs(a - amount) <= _TOL
        ):
            continue
        seen.append(amount)
    return seen


def waived_fees(text: str) -> list[dict[str, Any]]:
    """Fees the notes state and then cancel, for display in the breakdown.

    A waived fee is invisible in the arithmetic by design, which reads as an
    omission: the notes say $50 and the invoice mentions no $50 anywhere. Showing
    it as an explicit $0.00 line is the difference between "we handled this" and
    "did anyone notice?".

    Deduplicated on (amount, name) so the same waiver written in two note fields
    doesn't produce two lines.
    """
    out: list[dict[str, Any]] = []
    seen: set[tuple[float, str]] = set()
    for m in AMOUNT_RE.finditer(text):
        amount = _to_float(m.group(1))
        if amount <= 0 or not is_waived(text, m.start()):
            continue
        clause = clause_at(text, m.start()).strip()
        name = fee_name(clause)
        key = (round(amount, 2), name.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({"amount": round(amount, 2), "name": name, "phrase": clause})
    return out
