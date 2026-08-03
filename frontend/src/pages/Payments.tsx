import { useEffect, useRef, useState } from "react";
import {
  ApplyItem,
  ApplyResponse,
  ApplyResult,
  CashReview,
  CashReviewResponse,
  CheckReview,
  api,
} from "../api/client";
import { Empty, money } from "../components/ui";

/** Recording payments that arrive off-system: a check in the post, cash counted
 *  in a truck.
 *
 *  The happy path has no interaction in it at all. A photo of a check is read,
 *  matched, stripped of its 4% card fee and recorded; one recording of a day's
 *  takings posts every event it names. The screen's job on that path is to say
 *  what happened, not to ask for a confirmation of it — hence <Done>, which
 *  replaces the plan-and-approve card outright rather than sitting beside it.
 *
 *  What is left is the exceptions, and they are the reason this is a screen and
 *  not a background job. A check the matcher won't call, an amount that doesn't
 *  settle the invoice, an event nobody has processed yet: those come back with
 *  their near-misses and their scoring, editable and overrulable, and are
 *  applied as a batch. The rule the whole page turns on is that the system acts
 *  where the evidence is unambiguous and asks where it isn't — never the other
 *  way round, because a wrong match here marks another customer's invoice paid.
 *
 *  Nothing derived is computed here. The browser says which invoice and how
 *  much; the server recomputes the fee, the variance and the paid decision. */

type Tab = "check" | "cash";

/** One approved line waiting for Apply, with the words to describe it. */
interface BatchLine {
  key: string;
  item: ApplyItem;
  title: string;
  detail: string;
  warnings: string[];
}

/** Where a field's value came from, shown only when that is worth knowing.
 *
 *  Agreement is silent — annotating every row with "both agree" is noise that
 *  trains people to stop reading. What earns a line is KonaOS missing something
 *  we hold, or the two having drifted apart since we took our snapshot. */
function SourceNote({ source, other }: { source: string; other: string }) {
  if (!source || source === "both agree") return null;
  const drifted = source.startsWith("differs");
  return (
    <div
      className="muted"
      style={{ fontSize: 11, color: drifted ? "var(--warn)" : undefined }}
      title={other ? `KonaOS has: ${other}` : undefined}
    >
      {drifted ? `⚠ ${source}: ${other}` : source}
    </div>
  );
}

function Score({ flags }: { flags: string[] }) {
  return (
    <span className="muted" style={{ fontSize: 11 }}>
      {flags.join(" · ")}
    </span>
  );
}

/** What happened, for something that has already happened. Shown in place of a
 *  plan-and-approve card, because asking someone to confirm a payment already
 *  recorded is how the same check gets recorded twice. */
function Done({ result }: { result: ApplyResult }) {
  return (
    <div
      className="card"
      style={{
        background: "var(--surface-2)",
        marginTop: 10,
        borderLeft: `3px solid var(--${result.ok ? "ok" : "crit"})`,
      }}
    >
      <div style={{ fontWeight: 700 }}>
        {result.ok ? (result.dry_run ? "⚠ Dry run" : "✅ Recorded") : "❌ Not recorded"}
      </div>
      <p style={{ marginBottom: 0 }}>{result.summary}</p>
      {result.warnings?.map((w) => (
        <p key={w} className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
          ⚠ {w}
        </p>
      ))}
    </div>
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
        A check that arrived in the post, or cash counted at the truck. Photograph
        the check, or say the takings once — the system finds the invoice or the
        event, takes the 4% card fee off a check, and records it. Anything it
        can't settle on its own waits here for you rather than guessing.
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

/** A camera in the page, for photographing a check at a desk.
 *
 *  A file input with `capture` already opens the camera on a phone, but desktop
 *  browsers ignore that attribute entirely and show a file dialog — so on the
 *  machine the office actually sits at, "photograph a check" meant "go and find
 *  a photo somebody else took". This is the missing half.
 *
 *  Resolution is asked for deliberately: a check is read by OCR, and the amount
 *  and the payer are small print. A 640x480 webcam grab of a check is a wasted
 *  round trip. */
function CheckCamera({
  onPhoto,
  disabled,
}: {
  onPhoto: (file: File) => void;
  disabled: boolean;
}) {
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [error, setError] = useState("");
  const videoRef = useRef<HTMLVideoElement>(null);

  function stop() {
    setStream((current) => {
      current?.getTracks().forEach((t) => t.stop());
      return null;
    });
  }

  // The <video> only exists once `stream` is set, so the element is attached
  // here rather than at getUserMedia time — this effect runs after that render.
  useEffect(() => {
    if (stream && videoRef.current) {
      videoRef.current.srcObject = stream;
      void videoRef.current.play().catch(() => {});
    }
  }, [stream]);

  // A camera left running after the panel has gone is a live webcam with no UI
  // attached to it — and, on most laptops, an indicator light nobody can explain.
  useEffect(() => stop, []);

  async function open() {
    setError("");
    try {
      setStream(
        await navigator.mediaDevices.getUserMedia({
          video: {
            // A phone should use the rear camera; a laptop has only the one and
            // ignores this.
            facingMode: { ideal: "environment" },
            width: { ideal: 1920 },
            height: { ideal: 1080 },
          },
        })
      );
    } catch (e) {
      setError(
        (e as Error)?.name === "NotAllowedError"
          ? "The camera was blocked. Allow it for this site in the address bar, then try again."
          : "Couldn't open a camera. Upload a photo of the check instead."
      );
    }
  }

  function capture() {
    const video = videoRef.current;
    // videoWidth stays 0 until the first frame has actually arrived. Capturing
    // before that yields a 0x0 canvas, which toBlob turns into nothing at all —
    // a silent no-op on the one button the whole feature hangs off.
    if (!video?.videoWidth) {
      setError("The camera hasn't started yet — give it a second and try again.");
      return;
    }
    const canvas = document.createElement("canvas");
    // The video's own dimensions, not the size it is displayed at: the preview
    // is deliberately small and the photo must not be.
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d")?.drawImage(video, 0, 0);
    canvas.toBlob(
      (blob) => {
        stop();
        if (blob) onPhoto(new File([blob], "check.jpg", { type: "image/jpeg" }));
        else setError("That photo came out empty. Try again.");
      },
      "image/jpeg",
      0.92
    );
  }

  if (!stream) {
    return (
      <>
        <button className="btn primary" onClick={open} disabled={disabled}>
          📷 Take a photo
        </button>
        {error && (
          <span className="muted" style={{ color: "var(--crit)", fontSize: 12 }}>
            {error}
          </span>
        )}
      </>
    );
  }

  return (
    <div className="check-cam">
      <video ref={videoRef} playsInline muted />
      <div className="check-cam-actions">
        <button className="btn primary" onClick={capture}>
          ⬤ Capture
        </button>
        <button className="btn" onClick={stop}>
          Cancel
        </button>
        {/* The live view has to show errors too. Capture refuses when no frame
            has arrived yet, and without this that refusal was invisible — the
            button simply appeared to do nothing, which is worse than the
            failure it was guarding against. */}
        <span
          className="muted"
          style={{ fontSize: 12, color: error ? "var(--crit)" : undefined }}
        >
          {error || "Fill the frame with the check, flat and in focus."}
        </span>
      </div>
    </div>
  );
}

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
    // Once it has settled itself there is nothing left to correct, and leaving
    // the last check's payer in the box is how the next one gets re-matched
    // against the wrong name.
    const settled = !!next.applied?.ok;
    setPayer(settled ? "" : next.check.payer_name);
    setAmount(settled ? "" : next.check.amount ? String(next.check.amount) : "");
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
        invoice_number: review?.check.invoice_number,
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
  // Set when the upload settled itself. The plan-and-approve card is then the
  // wrong thing to show: it invites confirming a payment already recorded.
  const applied = review?.applied ?? null;
  const canCapture = captureIsPossible();
  // "Already paid" is the whole answer only when there is nothing to do with
  // this cheque — no invoice to settle and no split. When a real match sits
  // beside it, the same block would be flatly wrong ("nothing to do" beneath a
  // button that does something), so it becomes a caution instead.
  const paidHit = review?.already_paid ?? null;
  const settledIsTheAnswer =
    !!paidHit && !plan && !(review?.split?.length ?? 0);

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
        {/* Two ways in, because they cover different rooms. The in-page camera
            is for someone at a desk with the check in hand; the file input is
            for a phone (where `capture` opens the camera directly) and for a
            photo that already exists. The camera needs https, the file input
            does not — so on production only the second appears. */}
        {canCapture && <CheckCamera onPhoto={upload} disabled={!!busy} />}
        <button
          className={"btn" + (canCapture ? "" : " primary")}
          onClick={() => fileRef.current?.click()}
        >
          📷 {canCapture ? "Upload a photo" : "Photograph or upload a check"}
        </button>
        <span className="muted" style={{ fontSize: 12 }}>
          {busy || "Front of the check, flat and in focus. Nothing to type — it records itself."}
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

      {/* Everything the photo gave up, stated plainly and together. The date and
          the check number never feed the match — it goes on payer and amount —
          but they are how a person confirms this is the cheque in their hand
          before a payment is recorded against somebody's invoice. Hiding them
          next to a button, which is where they were, is not showing them. */}
      {review && !review.check.error && (
        <dl className="check-read">
          <div>
            <dt>Payer</dt>
            <dd>{review.check.payer_name || <span className="muted">not read</span>}</dd>
          </div>
          <div>
            <dt>Amount</dt>
            <dd>{review.check.amount ? money(review.check.amount) : <span className="muted">not read</span>}</dd>
          </div>
          <div>
            <dt>Date on the check</dt>
            <dd>{review.check.check_date || <span className="muted">not read</span>}</dd>
          </div>
          <div>
            <dt>Check number</dt>
            <dd>{review.check.check_number || <span className="muted">not read</span>}</dd>
          </div>
          {/* When present this decides the match outright, so it earns a place
              beside the fields that merely contribute to it. */}
          {review.check.invoice_number && (
            <div>
              <dt>Invoice number on the cheque</dt>
              <dd>{review.check.invoice_number}</dd>
            </div>
          )}
          {review.check.memo && (
            <div>
              <dt>Memo</dt>
              <dd>{review.check.memo}</dd>
            </div>
          )}
          {/* What the memo turned into. A memo naming "7/9 and 7/21" outranks
              the cheque's own date when matching, so showing the dates read out
              of it is showing the reasoning, not decoration. */}
          {review.check.memo_dates?.length > 0 && (
            <div>
              <dt>Events named in the memo</dt>
              <dd>{review.check.memo_dates.join(" · ")}</dd>
            </div>
          )}
        </dl>
      )}

      {/* The fallback, always on screen rather than only after a failed read.
          A photo normally fills these two and settles the check without them
          being looked at — but when it's blurry, when the camera isn't to
          hand, or when someone is working from the check itself, typing them
          has to be a way in rather than a dead end. */}
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
      </div>

      {applied && <Done result={applied} />}

      {/* Already recorded. Shown before anything else and in its own right,
          because the action here is "put the cheque down" — and a person told
          only that nothing matched goes looking for an invoice that was dealt
          with weeks ago, or worse, records the payment a second time. */}
      {settledIsTheAnswer && paidHit && (
        <div
          className="card"
          style={{
            background: "var(--surface-2)",
            marginTop: 12,
            borderLeft: "3px solid var(--ok)",
          }}
        >
          <div style={{ fontWeight: 700 }}>✅ Already paid — nothing to do</div>
          <p style={{ marginBottom: 4 }}>
            {paidHit.event_name || paidHit.business_name}
            {paidHit.event_date && (
              <span className="muted"> · {paidHit.event_date}</span>
            )}
          </p>
          <p className="muted" style={{ fontSize: 13, marginBottom: 0 }}>
            Invoice {paidHit.invoice_number || paidHit.id} ·{" "}
            {money(paidHit.grand_total)}
            {paidHit.total_without_fee != null && (
              <> · {money(paidHit.total_without_fee)} paid by check,
                after the 4% card fee came off</>
            )}
          </p>
        </div>
      )}

      {/* One cheque, several invoices. Shown ahead of the candidate list
          because when this appears the candidate list is asking the wrong
          question: not "which of these does it pay" but "these, together". */}
      {review && !applied && review.split?.length > 0 && (
        <div className="card" style={{ background: "var(--surface-2)", marginTop: 12 }}>
          <div style={{ fontWeight: 700, marginBottom: 6 }}>
            This cheque covers {review.split.length} invoices
          </div>
          <table style={{ fontSize: 13, width: "100%" }}>
            <tbody>
              {review.split.map((p) => (
                <tr key={p.invoice_id}>
                  <td>
                    {p.event_name || p.business_name}
                    {p.event_date && <span className="muted"> · {p.event_date}</span>}
                    <div className="muted" style={{ fontSize: 11 }}>
                      invoice {p.invoice_number || p.invoice_id}
                      {p.cc_fee_removed > 0 &&
                        ` · less ${money(p.cc_fee_removed)} card fee`}
                    </div>
                  </td>
                  <td style={{ textAlign: "right", verticalAlign: "top" }}>
                    {money(p.amount_due_after_fee)}
                  </td>
                </tr>
              ))}
              <tr style={{ fontWeight: 700 }}>
                <td>Total</td>
                <td style={{ textAlign: "right" }}>{money(review.split_total)}</td>
              </tr>
              <tr>
                <td className="muted">Cheque is for</td>
                <td className="muted" style={{ textAlign: "right" }}>
                  {money(review.check.amount)}
                </td>
              </tr>
            </tbody>
          </table>
          <button
            className="btn primary"
            style={{ marginTop: 12 }}
            onClick={() =>
              onApprove({
                key: `split:${review.split.map((p) => p.invoice_id).join(",")}`,
                item: {
                  kind: "check",
                  amount: review.check.amount,
                  invoice_ids: review.split.map((p) => p.invoice_id),
                  payer_name: payer,
                },
                title: `Check · ${review.check.payer_name}`,
                detail:
                  `${money(review.check.amount)} across ${review.split.length} ` +
                  `invoices: ${review.split
                    .map((p) => p.event_name || p.invoice_number)
                    .join(", ")}`,
                warnings: [],
              })
            }
          >
            Approve — settle all {review.split.length} →
          </button>
        </div>
      )}

      {/* An already-paid invoice noted BESIDE a live match — a caution, not a
          conclusion. Same payer, already settled: worth a second look before
          recording, in case this is that cheque arriving twice. */}
      {paidHit && !settledIsTheAnswer && (
        <p className="muted" style={{ color: "var(--warn)", marginTop: 12 }}>
          ⚠ Invoice {paidHit.invoice_number || paidHit.id} for{" "}
          {paidHit.event_name || paidHit.business_name} is already paid. Check
          this isn't the same cheque before recording it.
        </p>
      )}

      {review && !applied && !settledIsTheAnswer && (
        <>
          <p style={{ marginTop: 14, marginBottom: 6 }}>{review.reason}</p>
          {review.held_because && (
            <p className="muted" style={{ color: "var(--warn)", marginTop: -2 }}>
              Not recorded automatically — {review.held_because}
            </p>
          )}

          {plan && (
            <div className="card" style={{ background: "var(--surface-2)", marginTop: 10 }}>
              <div style={{ fontWeight: 700, marginBottom: 2 }}>
                {plan.event_name || plan.business_name}
                {plan.event_date && (
                  <span className="muted" style={{ fontWeight: 400 }}>
                    {" · "}{plan.event_date}
                  </span>
                )}
              </div>
              {/* The invoice number identifies the document; the line above
                  identifies the job. Both, in that order — somebody checking
                  this recognises the event, then verifies the invoice. */}
              <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>
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
              {/* Notes, not warnings — stated plainly and without an alarm
                  glyph, because they are not reasons to stop. */}
              {plan.notes?.map((n) => (
                <p key={n} className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
                  {n}
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
                    <th>Event</th>
                    <th>Business</th>
                    <th>Event date</th>
                    <th>Total</th>
                    {/* A check is normally written for the fee-free figure, so
                        when nothing matched this is usually the column that
                        explains why — and the one that turns out to agree. */}
                    <th>If paid by check</th>
                    <th>Why it's here</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {review.candidates.map((c) => (
                    <tr key={c.id}>
                      <td className="keep">{c.invoice_number || c.id}</td>
                      <td>
                        {c.event_name || <span className="muted">—</span>}
                        <SourceNote
                          source={c.event_name_source}
                          other={c.event_name_konaos}
                        />
                      </td>
                      <td>{c.business_name}</td>
                      <td className="keep">
                        {c.event_date || <span className="muted">—</span>}
                        <SourceNote
                          source={c.event_date_source}
                          other={c.event_date_konaos}
                        />
                      </td>
                      <td className="keep">{money(c.grand_total)}</td>
                      <td className="keep">
                        {c.total_without_fee == null ? (
                          <span className="muted">—</span>
                        ) : (
                          money(c.total_without_fee)
                        )}
                      </td>
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

/** Whether this browser will hand us a camera or a microphone at all.
 *
 *  getUserMedia only exists on a secure context — https, or localhost. Served
 *  over plain http on an IP, as production is, Chrome does not expose
 *  navigator.mediaDevices in the first place, so there is no permission for the
 *  user to grant and no amount of clicking Allow will produce one. Detecting
 *  that up front matters: "couldn't reach the camera" after a failed attempt
 *  reads as a glitch to retry, when the honest answer is that this page needs
 *  to be on https before the button can ever work.
 *
 *  Note this is only about capturing INSIDE the page. A file input with
 *  `capture` opens the phone's own camera or recorder and works fine on http —
 *  which is why both panels keep one. */
function captureIsPossible(): boolean {
  return (
    typeof window !== "undefined" &&
    window.isSecureContext &&
    !!navigator.mediaDevices?.getUserMedia
  );
}

/** Longest we will record in one go. A day's takings is a sentence or two; a
 *  button left running in a pocket is an upload the transcriber refuses. */
const MAX_RECORD_SECONDS = 180;
/** How many level samples the waveform shows, and how often one is taken. */
const WAVE_BARS = 28;
const SAMPLE_MS = 90;

/** The extension has to match what MediaRecorder actually produced — Chrome
 *  gives webm/opus, Safari gives mp4 — because the extension is how the format
 *  is declared to the transcriber, and the server refuses what it can't read. */
function recordingFilename(mimeType: string): string {
  return mimeType.includes("mp4") || mimeType.includes("mpeg")
    ? "takings.mp4"
    : "takings.webm";
}

function clock(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

/** A voice note, the way a chat app does it: press record, watch the meter move,
 *  stop to send, or bin it and start again.
 *
 *  The meter is not decoration. Dictating into a page that gives no feedback,
 *  you cannot tell a muted microphone from a silent room until the transcript
 *  comes back empty — and by then whoever counted the cash has walked off. */
function VoiceRecorder({
  onAudio,
  disabled,
}: {
  onAudio: (file: File) => void;
  disabled: boolean;
}) {
  const [recording, setRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [levels, setLevels] = useState<number[]>([]);
  const [error, setError] = useState("");
  const rig = useRef<{
    rec: MediaRecorder;
    stream: MediaStream;
    ctx: AudioContext;
    timer: number;
  } | null>(null);
  // Set before stop() when a recording is being thrown away, so onstop knows not
  // to send it. A ref, not state: onstop reads it outside React's render.
  const binned = useRef(false);

  function teardown() {
    const r = rig.current;
    if (!r) return;
    window.clearInterval(r.timer);
    r.stream.getTracks().forEach((t) => t.stop());
    void r.ctx.close().catch(() => {});
    rig.current = null;
  }

  // A recorder still holding the microphone after its panel has gone is a live
  // mic with no UI attached to it. Release it on the way out, always.
  useEffect(() => teardown, []);

  async function start() {
    setError("");
    setLevels([]);
    setElapsed(0);
    binned.current = false;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const ctx = new AudioContext();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      ctx.createMediaStreamSource(stream).connect(analyser);
      const buf = new Uint8Array(analyser.fftSize);

      const chunks: BlobPart[] = [];
      const rec = new MediaRecorder(stream);
      rec.ondataavailable = (e) => {
        if (e.data.size) chunks.push(e.data);
      };
      rec.onstop = () => {
        const type = rec.mimeType || "audio/webm";
        teardown();
        setRecording(false);
        if (binned.current) return;
        const blob = new Blob(chunks, { type });
        if (!blob.size) {
          setError("That recording came out empty. Try again.");
          return;
        }
        onAudio(new File([blob], recordingFilename(type), { type }));
      };

      const startedAt = Date.now();
      const timer = window.setInterval(() => {
        analyser.getByteTimeDomainData(buf);
        // RMS around the 128 midpoint — loudness, not frequency.
        let sum = 0;
        for (const v of buf) sum += (v - 128) ** 2;
        const level = Math.min(1, Math.sqrt(sum / buf.length) / 40);
        setLevels((prev) => [...prev, level].slice(-WAVE_BARS));

        const secs = (Date.now() - startedAt) / 1000;
        setElapsed(secs);
        if (secs >= MAX_RECORD_SECONDS) rec.stop();
      }, SAMPLE_MS);

      rig.current = { rec, stream, ctx, timer };
      rec.start();
      setRecording(true);
    } catch (e) {
      teardown();
      setRecording(false);
      // The mic exists, but this browser or this person said no.
      setError(
        (e as Error)?.name === "NotAllowedError"
          ? "The microphone was blocked. Allow it for this site in the address bar, then press record again."
          : "Couldn't start recording. Upload a voice memo or type the takings."
      );
    }
  }

  function stopAndSend() {
    binned.current = false;
    rig.current?.rec.stop();
  }

  function bin() {
    binned.current = true;
    rig.current?.rec.stop();
    setRecording(false);
  }

  if (!recording) {
    return (
      <>
        <button className="btn primary" onClick={start} disabled={disabled}>
          🎙 Record the takings
        </button>
        {error && (
          <span className="muted" style={{ color: "var(--crit)", fontSize: 12 }}>
            {error}
          </span>
        )}
      </>
    );
  }

  return (
    <div className="voice-note">
      <button className="voice-bin" onClick={bin} title="Discard this recording">
        🗑
      </button>
      <span className="voice-dot" aria-hidden="true" />
      <span className="voice-time">{clock(elapsed)}</span>
      <div className="voice-wave" aria-hidden="true">
        {Array.from({ length: WAVE_BARS }, (_, i) => {
          // Right-aligned, so the newest sample sits nearest the send button —
          // the way a chat app scrolls its waveform.
          const level = levels[levels.length - WAVE_BARS + i] ?? 0;
          return <span key={i} style={{ height: `${Math.max(8, level * 100)}%` }} />;
        })}
      </div>
      <button className="btn primary voice-send" onClick={stopAndSend}>
        ➤ Send
      </button>
    </div>
  );
}

function CashPanel({ onApprove }: { onApprove: (line: BatchLine) => void }) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [typed, setTyped] = useState("");
  const [onDate, setOnDate] = useState("");
  const [result, setResult] = useState<CashReviewResponse | null>(null);
  const audioRef = useRef<HTMLInputElement>(null);
  const canRecord = captureIsPossible();

  async function sendAudio(file: File) {
    setBusy("Listening back…");
    setError("");
    try {
      const next = await api.reviewCashVoice(file, onDate);
      setResult(next);
      setTyped(next.transcript);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
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
        {/* Recording in the page needs https. Uploading a voice memo does not —
            it is an ordinary file upload — so on an insecure origin that is the
            path offered rather than a button that cannot work. Same endpoint,
            same result: talk once, everything posts. */}
        {canRecord && <VoiceRecorder onAudio={sendAudio} disabled={!!busy} />}

        <input
          ref={audioRef}
          type="file"
          accept="audio/*"
          capture
          style={{ display: "none" }}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) sendAudio(file);
            e.target.value = "";
          }}
        />
        {/* `capture` on an audio input opens the phone's own voice recorder
            rather than a file browser, so on a phone this button IS "record a
            voice note" — two taps, no files, and it works on http where the
            in-page recorder cannot exist. Worth naming honestly: "Upload a
            voice memo" describes the desktop half and hides the good half. */}
        <button
          className={"btn" + (canRecord ? "" : " primary")}
          onClick={() => audioRef.current?.click()}
        >
          🎤 {canRecord ? "Send a voice memo" : "Record on your phone"}
        </button>

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
          {busy || "One go covers several events — “Pikesville took seven bucks, Camp Lollipop was twelve fifty” records both."}
        </span>
      </div>

      {!canRecord && (
        <p className="muted" style={{ fontSize: 12, marginTop: 10, marginBottom: 0 }}>
          <strong>On a phone, that button opens your voice recorder — talk, tap
          done, and it posts.</strong> On a computer it asks for an audio file
          instead: recording inside the page needs the dashboard on{" "}
          <code>https</code>, and browsers won't hand a microphone to an
          unencrypted page, so there is no permission to grant here. Typing the
          takings below does exactly the same thing once it is read.
        </p>
      )}

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

      {result && result.items.length > 0 && (
        <p style={{ marginTop: 14, marginBottom: 0, fontWeight: 600 }}>
          {result.items.filter((i) => i.applied?.ok).length} of {result.items.length}{" "}
          recorded automatically
          {result.items.some((i) => !i.applied?.ok)
            ? " — the rest are below, they need a person."
            : "."}
        </p>
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
  // Posted off the recording. Re-matching it would only offer to post it again.
  const applied = state.applied;

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

  if (applied) {
    return (
      <div className="card" style={{ background: "var(--surface-2)", marginTop: 12 }}>
        <div style={{ fontWeight: 600 }}>
          “{state.heard.query}” → {state.event?.event_name}
        </div>
        <Done result={applied} />
      </div>
    );
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
      {state.held_because && !state.blocked && (
        <p className="muted" style={{ color: "var(--warn)", fontSize: 12 }}>
          Not recorded automatically — {state.held_because}
        </p>
      )}
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
                <th>Event date</th>
                <th>Where</th>
                <th>Why it's here</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {state.candidates.map((c) => (
                <tr key={c.id}>
                  <td>{c.name}</td>
                  {/* Its own column. Falling back to the town when the date was
                      missing printed "Baltimore" under a heading that said
                      DATE — which reads as data and is simply false. */}
                  <td className="keep">
                    {c.event_date || <span className="muted">—</span>}
                  </td>
                  <td>{c.city || <span className="muted">—</span>}</td>
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
        <Empty text="Anything the system wouldn't record on its own collects here — approve it above and it lands in one go." />
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
