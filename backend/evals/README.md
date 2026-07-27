# Classifier eval harness

Measures whether the LLM classifier reads real event notes correctly, so prompt
edits and model swaps become a number instead of a hunch.

```bash
cd backend
export OPENAI_API_KEY=...          # these cases call the real API
python evals/run_eval.py           # score confirmed cases on the configured model
```

Useful flags:

| Flag | Why |
|---|---|
| `--dry` | Parse and list the cases without spending a token. Start here. |
| `--model <id>` | Score a different model. This is the A/B switch. |
| `--repeat 3` | Classify each case N times. `seed=42` is best-effort, so a case that passes once may not pass always — this is how you catch a `FLAKY`. |
| `--case <substr>` | Run one case while iterating on the prompt. |
| `--include-unconfirmed` | Also score cases whose expected values nobody has signed off on yet. |
| `--in-cost` / `--out-cost` | Correct the cost line when `--model` has different rates. |

Exit status is non-zero if any case fails, so this can gate a change.

## What a case asserts

Two layers, both optional, and only the keys you name are checked:

- **`expect`** — classification fields (`BILLING_MODEL`, `BASE_AMOUNT`, …).
- **`expect_invoice`** — what `billing.calculate_invoice()` produces from that
  classification.

`expect_invoice` is the one that matters. A case can get every field right and
still bill the wrong number, and the number is what reaches the client.

Comparison is lenient about representation and strict about value: `1200`,
`1200.0`, `"1200"` and `"$1,200"` all match, money is compared to the cent, and
the `TRUE`/`YES` string enums are case-insensitive. So a case never fails over
formatting drift.

## Adding a case

Every case should be a **real event that was billed wrong**, not a synthetic one.
Synthetic cases test the prompt against your own assumptions; real ones test it
against how admins and drivers actually write.

1. Copy an existing file in `cases/`.
2. Paste the real `cleaned` payload (the pipeline stores one per event, ~3 KB).
   **Redact contact phone numbers and street addresses** — they never affect
   classification, and this file gets committed.
3. Pin the fields that were wrong in `expect`, and the money in `expect_invoice`.
4. Explain the bug in `why`. Future-you needs to know why the case exists.
5. Set `"confirmed": false` until someone who owns the billing decision agrees
   with the expected numbers.

### On `confirmed`

An unconfirmed case encodes somebody's *reading* of ambiguous notes. Left
unmarked, that reading quietly becomes the definition of correct and the eval
starts certifying an assumption. Unconfirmed cases are skipped by default and
listed as `SKIP` so they stay visible instead of being forgotten.

## Why this is not in `tests/`

Two reasons, either sufficient:

- `tests/conftest.py` sets `OPENAI_PROVIDER=mock` for everything it collects, so
  a pytest-based eval would score the mock, not the model.
- Every run costs real tokens. CI runs on every push; this should not.
