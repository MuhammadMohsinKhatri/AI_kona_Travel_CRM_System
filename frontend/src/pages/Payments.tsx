import { useRef, useState } from "react";
import {
  ApplyItem,
  ApplyResponse,
  CashReview,
  CashReviewResponse,
  CheckReview,
  api,
} from "../api/client";
import { Empty, money } from "../components/ui";

/** Recording payments that arrive off-system: a check in the post, cash counted
 *  in a truck.
 *
 *  Two Telegram bots used to do this, and both wrote immediately — a model read
 *  the photo or the sentence and the payment was recorded, so a misread amount
 *  became a payment against the wrong customer, found whenever somebody next
 *  looked. Here the reading and the writing are separated by this screen. Every
 *  figure below is editable, every match can be overruled, and nothing reaches
 *  KonaOS until Apply.
 *
 *  Apply is one click for the whole batch, not one per line — but it is still a
 *  click, and the server recomputes every derived figure when it runs. This
 *  screen can only say which invoice and how much. */

type Tab = "check" | "cash";

/** One approved line waiting for Apply, with the words to describe it. */
interface BatchLine {
  key: string;
  item: ApplyItem;
  title: string;
  detail: string;
  warnings: string[];
}

function Score({ flags }: { flags: string[] }) {
  return (
    <span className="muted" style={{ fontSize: 11 }}>
      {flags.join(" · ")}
    </span>
  );
}

export default function Payments() {
  const [tab, setTab] = useState<Tab>("check");
  const [batch, setBatch] = useState<BatchLine[]>([]);
  const [applying, setApplying] = useState(false);
  const [applied, setApplied] = useState<ApplyResponse | null>(null);
  // Bumped after anything applies, to remount the panels below. A review left
  // on screen after its payment went through is an invitation to approve the
  // same check twice — the server refuses it, but the screen shouldn't offer.
  const [round, setRound] = useState(0);

  function addToBatch(line: BatchLine) {
    // Same invoice or same event twice in one batch is a double payment.
    setBatch((prev) => [...prev.filter((l) => l.key !== line.key), line]);
    setApplied(null);
  }

  async function applyAll() {
    setApplying(true);
    try {
      const result = await api.applyPayments(batch.map((l) => l.item));
      setApplied(result);
      // Keep only the lines that didn't go through, so a retry is one click
      // and nothing that succeeded can be applied twice.
      const failedKeys = new Set(
        result.results
          .map((r, i) => (r.ok ? null : batch[i]?.key))
          .filter((k): k is string => !!k)
      );
      setBatch((prev) => prev.filter((l) => failedKeys.has(l.key)));
      if (result.applied > 0) setRound((n) => n + 1);
    } catch (e) {
      setApplied({
        applied: 0,
        failed: batch.length,
        dry_run: false,
        results: [
          { ok: false, kind: "check", summary: (e as Error).message ?? "Failed" },
        ],
      });
    } finally {
      setApplying(false);
    }
  }

  return (
    <>
      <h1 className="page-title">Record Payments</h1>
      <p className="page-sub">
        A check that arrived in the post, or cash counted at the truck. Upload or
        dictate it, check what was read, then apply the lot in one click. Nothing
        is written to KonaOS until you press Apply.
      </p>

      <div className="toolbar" style={{ gap: 8, marginBottom: 16 }}>
        <button
          className={"btn" + (tab === "check" ? " primary" : "")}
          onClick={() => setTab("check")}
        >
          🧾 Check in the post
        </button>
        <button
          className={"btn" + (tab === "cash" ? " primary" : "")}
          onClick={() => setTab("cash")}
        >
          💵 Cash counted
        </button>
      </div>

      {tab === "check" ? (
        <CheckPanel key={round} onApprove={addToBatch} />
      ) : (
        <CashPanel key={round} onApprove={addToBatch} />
      )}

      <BatchPanel
        batch={batch}
        applying={applying}
        applied={applied}
        onApply={applyAll}
        onRemove={(key) => setBatch((prev) => prev.filter((l) => l.key !== key))}
      />
    </>
  );
}

// ── checks ──────────────────────────────────────────────────────────────────

function CheckPanel({ onApprove }: { onApprove: (line: BatchLine) => void }) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [review, setReview] = useState<CheckReview | null>(null);
  // The corrected details, which are what gets re-matched — never the model's
  // reading once a person has touched it.
  const [payer, setPayer] = useState("");
  const [amount, setAmount] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  function load(next: CheckReview) {
    setReview(next);
    setPayer(next.check.payer_name);
    setAmount(next.check.amount ? String(next.check.amount) : "");
  }

  async function upload(file: File) {
    setBusy("Reading the check…");
    setError("");
    try {
      load(await api.reviewCheckPhoto(file));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function rematch(invoiceId = "") {
    setBusy("Matching…");
    setError("");
    try {
      const next = await api.rematchCheck({
        payer_name: payer,
        amount: Number(amount) || 0,
        check_date: review?.check.check_date,
        check_number: review?.check.check_number,
        memo: review?.check.memo,
        invoice_id: invoiceId,
      });
      // Keep what's in the boxes — the reviewer typed it, it isn't stale.
      setReview({ ...next, check: { ...next.check, payer_name: payer } });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }

  const plan = review?.plan ?? null;

  return (
    <div className="card" style={{ marginBottom: 18 }}>
      <div className="flex" style={{ gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          capture="environment"
          style={{ display: "none" }}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) upload(file);
            e.target.value = "";
          }}
        />
        <button className="btn primary" onClick={() => fileRef.current?.click()}>
          📷 Photograph or upload a check
        </button>
        <span className="muted" style={{ fontSize: 12 }}>
          {busy || "Front of the check, flat and in focus."}
        </span>
      </div>

      {error && <p className="muted" style={{ color: "var(--crit)" }}>{error}</p>}

      <hr style={{ margin: "16px 0", border: 0, borderTop: "1px solid var(--border)" }} />

      {review?.check.error && (
        <p className="muted" style={{ color: "var(--warn)" }}>
          {review.check.error} Type the payer and the amount below instead.
        </p>
      )}
      {review?.check.notes && (
        <p className="muted" style={{ fontSize: 12 }}>
          Read with {review.check.confidence} confidence — {review.check.notes}
        </p>
      )}

      {/* Always on screen, not just after a successful read. A photo is a
          shortcut to filling these two boxes — when it's blurry, when the
          camera isn't to hand, or when someone is working from the check
          itself, typing them has to be a first-class way in rather than a
          dead end. */}
      <div className="flex" style={{ gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
        <label style={{ fontSize: 12 }}>
          <div className="muted">Payer (top-left of the check)</div>
          <input
            className="input"
            style={{ width: 280 }}
            placeholder="e.g. Jones Elementary School PTA"
            value={payer}
            onChange={(e) => setPayer(e.target.value)}
          />
        </label>
        <label style={{ fontSize: 12 }}>
          <div className="muted">Amount</div>
          <input
            className="input"
            style={{ width: 120 }}
            inputMode="decimal"
            placeholder="0.00"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
          />
        </label>
        <button
          className="btn"
          onClick={() => rematch()}
          disabled={!!busy || !payer.trim() || !Number(amount)}
        >
          {review ? "Re-match" : "Find the invoice"}
        </button>
        {review?.check.check_number && (
          <span className="muted" style={{ fontSize: 12 }}>
            Check #{review.check.check_number}
            {review.check.check_date ? ` · ${review.check.check_date}` : ""}
          </span>
        )}
      </div>

      {review && (
        <>
          <p style={{ marginTop: 14, marginBottom: 6 }}>{review.reason}</p>

          {plan && (
            <div className="card" style={{ background: "var(--surface-2)", marginTop: 10 }}>
              <div style={{ fontWeight: 700, marginBottom: 6 }}>
                {plan.business_name} · invoice {plan.invoice_number || plan.invoice_id}
              </div>
              <table style={{ fontSize: 13 }}>
                <tbody>
                  <tr>
                    <td className="muted" style={{ paddingRight: 14 }}>Invoice in KonaOS now</td>
                    <td>{money(plan.invoice_total)}</td>
                  </tr>
                  <tr>
                    <td className="muted" style={{ paddingRight: 14 }}>
                      Less the 4% card fee (a check doesn't incur it)
                    </td>
                    <td>−{money(plan.cc_fee_removed)}</td>
                  </tr>
                  <tr style={{ fontWeight: 700 }}>
                    <td style={{ paddingRight: 14 }}>Client owes</td>
                    <td>{money(plan.amount_due_after_fee)}</td>
                  </tr>
                  <tr>
                    <td className="muted" style={{ paddingRight: 14 }}>Check is for</td>
                    <td>{money(plan.check_amount)}</td>
                  </tr>
                </tbody>
              </table>
              <div style={{ marginTop: 10 }}>
                {plan.status === "exact" && <span className="badge green">Pays in full</span>}
                {plan.status === "underpaid" && (
                  <span className="badge amber">
                    Short by {money(Math.abs(plan.variance))} — balance stays open
                  </span>
                )}
                {plan.status === "overpaid" && (
                  <span className="badge amber">Over by {money(plan.variance)}</span>
                )}
              </div>
              {plan.warnings.map((w) => (
                <p key={w} className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
                  ⚠ {w}
                </p>
              ))}
              <button
                className="btn primary"
                style={{ marginTop: 12 }}
                onClick={() =>
                  onApprove({
                    key: `check:${plan.invoice_id}`,
                    item: {
                      kind: "check",
                      amount: plan.check_amount,
                      invoice_id: plan.invoice_id,
                      payer_name: payer,
                    },
                    title: `Check · ${plan.business_name}`,
                    detail:
                      `${money(plan.check_amount)} against invoice ` +
                      `${plan.invoice_number || plan.invoice_id}` +
                      (plan.cc_fee_removed > 0
                        ? ` — 4% fee (${money(plan.cc_fee_removed)}) comes off first`
                        : ""),
                    warnings: plan.warnings,
                  })
                }
              >
                Approve this check →
              </button>
            </div>
          )}

          {review.candidates.length > 0 && !plan && (
            <div className="table-wrap" style={{ marginTop: 10 }}>
              <table>
                <thead>
                  <tr>
                    <th>Invoice</th>
                    <th>Business</th>
                    <th>Total</th>
                    <th>Why it's here</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {review.candidates.map((c) => (
                    <tr key={c.id}>
                      <td>{c.invoice_number || c.id}</td>
                      <td>{c.business_name}</td>
                      <td>{money(c.grand_total)}</td>
                      <td><Score flags={c.flags} /></td>
                      <td>
                        <button className="btn" onClick={() => rematch(c.id)}>
                          It pays this one
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ── cash ────────────────────────────────────────────────────────────────────

function CashPanel({ onApprove }: { onApprove: (line: BatchLine) => void }) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [recording, setRecording] = useState(false);
  const [typed, setTyped] = useState("");
  const [onDate, setOnDate] = useState("");
  const [result, setResult] = useState<CashReviewResponse | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);

  async function startRecording() {
    setError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chunks: BlobPart[] = [];
      const rec = new MediaRecorder(stream);
      rec.ondataavailable = (e) => chunks.push(e.data);
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunks, { type: "audio/webm" });
        setBusy("Listening back…");
        try {
          const next = await api.reviewCashVoice(
            new File([blob], "speech.webm", { type: "audio/webm" }),
            onDate
          );
          setResult(next);
          setTyped(next.transcript);
        } catch (e) {
          setError((e as Error).message);
        } finally {
          setBusy("");
        }
      };
      recorder.current = rec;
      rec.start();
      setRecording(true);
    } catch {
      setError(
        "Couldn't reach the microphone. Type what was taken in the box instead."
      );
    }
  }

  function stopRecording() {
    recorder.current?.stop();
    setRecording(false);
  }

  async function readTyped() {
    if (!typed.trim()) return;
    setBusy("Reading it back…");
    setError("");
    try {
      setResult(await api.reviewCashText(typed, onDate));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="card" style={{ marginBottom: 18 }}>
      <div className="flex" style={{ gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        {recording ? (
          <button className="btn danger" onClick={stopRecording}>
            ⏹ Stop and read it back
          </button>
        ) : (
          <button className="btn primary" onClick={startRecording}>
            🎙 Record the takings
          </button>
        )}
        <label className="muted" style={{ fontSize: 12 }}>
          Events on{" "}
          <input
            className="input"
            type="date"
            style={{ width: 150 }}
            value={onDate}
            onChange={(e) => setOnDate(e.target.value)}
            title="Which day's events to search when the recording doesn't say"
          />
        </label>
        <span className="muted" style={{ fontSize: 12 }}>
          {busy || "One go covers several events — “Pikesville took seven bucks, Camp Lollipop was twelve fifty”."}
        </span>
      </div>

      <div style={{ marginTop: 12 }}>
        <textarea
          className="input"
          style={{ width: "100%", minHeight: 60 }}
          placeholder="…or type what was taken, if the mic isn't an option."
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
        />
        <button className="btn" style={{ marginTop: 8 }} onClick={readTyped} disabled={!!busy}>
          Read this back
        </button>
        <span className="muted" style={{ fontSize: 12, marginLeft: 10 }}>
          For a single event you already have open, Event Financials edits its
          cash figure directly.
        </span>
      </div>

      {error && <p className="muted" style={{ color: "var(--crit)" }}>{error}</p>}
      {result?.error && <p className="muted" style={{ color: "var(--warn)" }}>{result.error}</p>}
      {result?.notes && (
        <p className="muted" style={{ fontSize: 12 }}>Heard, with a caveat: {result.notes}</p>
      )}

      {result?.items.map((item, i) => (
        <CashLineCard
          key={`${item.heard.query}-${i}`}
          line={item}
          fallbackDate={onDate}
          onApprove={onApprove}
        />
      ))}
      {result && result.items.length === 0 && !result.error && (
        <Empty text="Nothing in that recording named an event and an amount." />
      )}
    </div>
  );
}

function CashLineCard({
  line,
  fallbackDate,
  onApprove,
}: {
  line: CashReview;
  fallbackDate: string;
  onApprove: (line: BatchLine) => void;
}) {
  const [state, setState] = useState(line);
  const [amount, setAmount] = useState(line.heard.amount ? String(line.heard.amount) : "");
  const [busy, setBusy] = useState(false);

  async function rematch(crmEventId = "") {
    setBusy(true);
    try {
      setState(
        await api.rematchCash({
          query: state.heard.query,
          amount: Number(amount) || 0,
          brand: state.heard.brand,
          date: state.heard.date || fallbackDate,
          crm_event_id: crmEventId,
        })
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ background: "var(--surface-2)", marginTop: 12 }}>
      <div className="flex" style={{ gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
        <label style={{ fontSize: 12 }}>
          <div className="muted">Heard</div>
          <div style={{ fontWeight: 600, minWidth: 200 }}>
            {state.heard.query || <span className="muted">— no event named —</span>}
          </div>
        </label>
        <label style={{ fontSize: 12 }}>
          <div className="muted">Cash taken</div>
          <input
            className="input"
            style={{ width: 110 }}
            inputMode="decimal"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
          />
        </label>
        <button className="btn" onClick={() => rematch()} disabled={busy}>
          Re-match
        </button>
      </div>

      <p style={{ marginTop: 10, marginBottom: 6 }}>{state.reason}</p>
      {state.blocked && (
        <p className="muted" style={{ color: "var(--warn)", fontSize: 12 }}>⚠ {state.blocked}</p>
      )}

      {state.event && (
        <div style={{ fontSize: 13 }}>
          <strong>{state.event.event_name}</strong>{" "}
          <span className="muted">
            {state.event.event_date} · {state.event.brand}
          </span>
          {state.previous_cash > 0 && (
            <span className="muted">
              {" "}· already recorded: {money(state.previous_cash)} (this replaces it)
            </span>
          )}
        </div>
      )}

      {state.ready && state.event && (
        <button
          className="btn primary"
          style={{ marginTop: 10 }}
          onClick={() =>
            onApprove({
              key: `cash:${state.event!.crm_event_id}`,
              item: {
                kind: "cash",
                amount: Number(amount) || 0,
                crm_event_id: state.event!.crm_event_id,
              },
              title: `Cash · ${state.event!.event_name}`,
              detail: `${money(Number(amount) || 0)} recorded against ${state.event!.event_date}`,
              warnings: [],
            })
          }
        >
          Approve this line →
        </button>
      )}

      {state.candidates.length > 0 && !state.ready && (
        <div className="table-wrap" style={{ marginTop: 10 }}>
          <table>
            <thead>
              <tr>
                <th>Event</th>
                <th>Date</th>
                <th>Why it's here</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {state.candidates.map((c) => (
                <tr key={c.id}>
                  <td>{c.name}</td>
                  <td>{c.event_date || c.city}</td>
                  <td><Score flags={c.flags} /></td>
                  <td>
                    <button className="btn" onClick={() => rematch(c.id)} disabled={busy}>
                      It's this one
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── the batch ───────────────────────────────────────────────────────────────

function BatchPanel({
  batch,
  applying,
  applied,
  onApply,
  onRemove,
}: {
  batch: BatchLine[];
  applying: boolean;
  applied: ApplyResponse | null;
  onApply: () => void;
  onRemove: (key: string) => void;
}) {
  return (
    <div className="card">
      <div className="flex" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <h2 style={{ margin: 0, fontSize: 16 }}>Ready to apply ({batch.length})</h2>
        <button
          className="btn primary"
          disabled={!batch.length || applying}
          onClick={onApply}
        >
          {applying ? "Applying…" : `Apply all ${batch.length || ""}`}
        </button>
      </div>

      {batch.length === 0 ? (
        <Empty text="Approved checks and cash lines collect here. Apply writes them to KonaOS in one go." />
      ) : (
        <div className="table-wrap" style={{ marginTop: 12 }}>
          <table>
            <tbody>
              {batch.map((l) => (
                <tr key={l.key}>
                  <td style={{ fontWeight: 600 }}>{l.title}</td>
                  <td>
                    {l.detail}
                    {l.warnings.map((w) => (
                      <div key={w} className="muted" style={{ fontSize: 12 }}>⚠ {w}</div>
                    ))}
                  </td>
                  <td style={{ textAlign: "right" }}>
                    <button className="btn" onClick={() => onRemove(l.key)}>
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {applied && (
        <div style={{ marginTop: 14 }}>
          <p style={{ marginBottom: 6 }}>
            {applied.dry_run && <span className="badge amber">Dry run — nothing was written</span>}{" "}
            {applied.applied} applied, {applied.failed} not.
          </p>
          {applied.results.map((r, i) => (
            <p key={i} className="muted" style={{ fontSize: 13, marginBottom: 4 }}>
              {r.ok ? "✅" : "❌"} {r.summary}
              {r.warnings?.map((w) => (
                <span key={w} style={{ display: "block" }}>⚠ {w}</span>
              ))}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
