"""Classifier accuracy harness — scores the LLM classifier against labelled real events.

Deliberately NOT a pytest suite. ``tests/conftest.py`` forces
``OPENAI_PROVIDER=mock`` for everything it collects, and every case here costs
real OpenAI tokens. Run it by hand when you change the prompt or the model.

    cd backend
    python evals/run_eval.py                    # current model, confirmed cases
    python evals/run_eval.py --model gpt-5      # A/B a different model
    python evals/run_eval.py --repeat 3         # measure run-to-run drift
    python evals/run_eval.py --dry              # validate case files, no API calls

Each case asserts two layers:

  ``expect``          individual classification fields (BILLING_MODEL, BASE_AMOUNT, …)
  ``expect_invoice``  the money that falls out of billing.calculate_invoice()

The second layer is the one that matters. A case can get every field right and
still bill the wrong number, and it is the number that reaches the client.

Exit status is non-zero if any case fails, so "did the pass rate hold" can gate
a prompt edit or a model swap instead of being a hunch.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

# The classifier only needs OPENAI_*; keep the rest of the app's settings from
# reaching for a database that isn't there when this runs on a laptop.
os.environ.setdefault("DATABASE_URL", "sqlite:///./eval_scratch.db")
os.environ.setdefault("CRM_PROVIDER", "mock")
os.environ.setdefault("SQUARE_PROVIDER", "mock")
os.environ.setdefault("TELEGRAM_PROVIDER", "mock")

CASES_DIR = Path(__file__).resolve().parent / "cases"
TOLERANCE = 0.005  # cent-level; money fields are rounded to 2dp upstream


# ── comparison ────────────────────────────────────────────────────────────────

def _norm(v: Any) -> Any:
    """Compare like the billing engine reads: numbers as numbers, the
    TRUE/FALSE/YES/NO string enums case-insensitively, everything else as
    stripped lowercase text. Without this, ``1200`` vs ``1200.0`` vs ``"1200"``
    read as three different answers when they all bill identically."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    try:
        return float(s.replace(",", "").lstrip("$"))
    except ValueError:
        return s.lower()


def _matches(expected: Any, actual: Any) -> bool:
    e, a = _norm(expected), _norm(actual)
    if isinstance(e, float) and isinstance(a, float):
        return abs(e - a) <= TOLERANCE
    return e == a


def _diff(expect: dict[str, Any], got: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    """Only the keys the case names are checked — a case pins the fields it
    cares about, not all ~40, so adding a field to the schema never
    retroactively fails an unrelated case."""
    return [
        (k, v, got.get(k, "<missing>"))
        for k, v in expect.items()
        if not _matches(v, got.get(k))
    ]


# ── cases ─────────────────────────────────────────────────────────────────────

def load_cases(include_unconfirmed: bool, only: str | None) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in sorted(CASES_DIR.glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        case["_file"] = path.name
        if only and only.lower() not in path.stem.lower():
            continue
        # An unconfirmed case encodes someone's *reading* of ambiguous notes.
        # Those must not silently become the definition of correct — a human
        # signs off by flipping "confirmed" to true.
        if not case.get("confirmed", False) and not include_unconfirmed:
            case["_skipped"] = "unconfirmed — pass --include-unconfirmed to score it"
        cases.append(case)
    return cases


def _say(message: str) -> None:
    """Progress goes out immediately. Without this the whole run printed nothing
    until the final report, so a slow pass looked identical to a hang."""
    print(message, flush=True)


def score_case(case: dict[str, Any], classifier, repeat: int,
               position: str = "") -> dict[str, Any]:
    from app.core import billing

    try:
        from app.core.pipeline import _normalize_classification
    except Exception:  # pragma: no cover - keep the harness usable if that moves
        def _normalize_classification(c):  # type: ignore[misc]
            return c

    label = case.get("name") or case["_file"]
    _say(f"  {position}{label}")

    attempts: list[dict[str, Any]] = []
    for attempt in range(1, repeat + 1):
        _say(f"      attempt {attempt}/{repeat} — calling the model…")
        raw = classifier.classify(case["cleaned"])
        usage = raw.get("_usage") or {}
        classification = _normalize_classification(raw)
        calc = billing.calculate_invoice(classification)

        field_diff = _diff(case.get("expect", {}), classification)
        money_diff = _diff(case.get("expect_invoice", {}), calc)
        ok = not field_diff and not money_diff
        attempts.append({
            "ok": ok,
            "field_diff": field_diff,
            "money_diff": money_diff,
            "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
        })
        _say(
            f"      attempt {attempt}/{repeat} — {'pass' if ok else 'FAIL'}"
            f" · {classification.get('BILLING_MODEL', '?')}"
            f" · subtotal {calc.get('SUBTOTAL')}"
        )

    return {
        "name": case.get("name") or case["_file"],
        "file": case["_file"],
        "attempts": attempts,
        "passes": sum(1 for a in attempts if a["ok"]),
    }


# ── reporting ─────────────────────────────────────────────────────────────────

def report(results: list[dict[str, Any]], skipped: list[dict[str, Any]],
           repeat: int, in_cost: float, out_cost: float, model: str) -> bool:
    print(f"\nmodel: {model}   cases: {len(results)}   attempts each: {repeat}\n")

    all_green = True
    for r in results:
        stable = r["passes"] == repeat
        mark = "PASS" if stable else ("FLAKY" if r["passes"] else "FAIL")
        suffix = "" if repeat == 1 else f"  ({r['passes']}/{repeat})"
        print(f"  [{mark:5}] {r['name']}{suffix}")
        if not stable:
            all_green = False
            # One representative failure is enough to act on; dumping all
            # N attempts buries the signal when --repeat is high.
            worst = next(a for a in r["attempts"] if not a["ok"])
            for label, diffs in (("field", worst["field_diff"]),
                                 ("money", worst["money_diff"])):
                for key, exp, got in diffs:
                    print(f"           {label} {key}: expected {exp!r}, got {got!r}")

    for s in skipped:
        print(f"  [SKIP ] {s.get('name') or s['_file']} — {s['_skipped']}")

    p_tok = sum(a["prompt_tokens"] for r in results for a in r["attempts"])
    c_tok = sum(a["completion_tokens"] for r in results for a in r["attempts"])
    cost = p_tok / 1e6 * in_cost + c_tok / 1e6 * out_cost
    total = len(results) * repeat
    passed = sum(r["passes"] for r in results)
    rate = (passed / total * 100) if total else 0.0

    print(f"\n  pass rate: {passed}/{total} ({rate:.0f}%)")
    print(f"  tokens: {p_tok:,} in / {c_tok:,} out   cost: ${cost:.4f}")
    if skipped:
        print(f"  {len(skipped)} case(s) skipped as unconfirmed")
    print()
    return all_green


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", help="override OPENAI_MODEL for this run (A/B a model)")
    ap.add_argument("--repeat", type=int, default=1,
                    help="classify each case N times to expose run-to-run drift "
                         "(seed=42 is best-effort, not a guarantee)")
    ap.add_argument("--case", help="substring filter on the case filename")
    ap.add_argument("--include-unconfirmed", action="store_true",
                    help="also score cases whose expected values are not signed off")
    ap.add_argument("--dry", action="store_true",
                    help="validate the case files without calling the API")
    ap.add_argument("--in-cost", type=float, default=None,
                    help="input $/Mtok for the cost line (default: configured rate)")
    ap.add_argument("--out-cost", type=float, default=None,
                    help="output $/Mtok for the cost line (default: configured rate)")
    ap.add_argument("--timeout", type=float, default=90.0,
                    help="seconds before a single model call is abandoned "
                         "(default 90) — a hung request must not stall the run")
    args = ap.parse_args()

    if args.repeat < 1:
        print("--repeat must be at least 1", file=sys.stderr)
        return 2

    cases = load_cases(args.include_unconfirmed, args.case)
    if not cases:
        print(f"No cases found in {CASES_DIR}", file=sys.stderr)
        return 2

    runnable = [c for c in cases if "_skipped" not in c]
    skipped = [c for c in cases if "_skipped" in c]

    from app.config import settings
    in_cost = args.in_cost if args.in_cost is not None else settings.openai_input_cost_per_mtok
    out_cost = args.out_cost if args.out_cost is not None else settings.openai_output_cost_per_mtok
    model = args.model or settings.openai_model

    if args.dry:
        print(f"\n{len(cases)} case file(s) parsed OK "
              f"({len(runnable)} runnable, {len(skipped)} unconfirmed):\n")
        for c in cases:
            fields = len(c.get("expect", {})) + len(c.get("expect_invoice", {}))
            flag = "" if "_skipped" in c else " *"
            print(f"  {c['_file']}{flag}  {fields} assertion(s)")
            if not c.get("cleaned"):
                print("     WARNING: no 'cleaned' payload — this case cannot run")
        print("\n  * = would be scored; run without --dry to call the API\n")
        return 0

    if not (settings.openai_api_key or "").strip():
        print("OPENAI_API_KEY is not set — export it before running the eval "
              "(these cases call the real API).", file=sys.stderr)
        return 2

    from app.integrations.live import OpenAIClassifier

    classifier = OpenAIClassifier()
    if args.model:
        classifier.model = args.model
    # The production classifier has no timeout — fine for a Celery task that can
    # be retried, wrong for an interactive run where a stuck call just hangs.
    try:
        classifier.client = classifier.client.with_options(timeout=args.timeout)
    except Exception:  # pragma: no cover - older SDKs lack with_options
        pass

    total_calls = len(runnable) * args.repeat
    _say(f"\nmodel: {model}   {len(runnable)} case(s) x {args.repeat} "
         f"= {total_calls} call(s), timeout {args.timeout:.0f}s each\n")

    results = []
    for i, case in enumerate(runnable, 1):
        results.append(
            score_case(case, classifier, args.repeat, position=f"[{i}/{len(runnable)}] ")
        )

    ok = report(results, skipped, args.repeat, in_cost, out_cost, model)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
