import { useEffect, useRef, useState } from "react";
import {
  AimeeBudget,
  AimeeCapabilities,
  AimeeConversation,
  AimeeMessage,
  api,
} from "../api/client";
import { DeleteButton, money } from "../components/ui";

/** Aimee — the chat assistant.
 *
 *  Three things shape this screen, all of them lessons from the Telegram bot it
 *  replaces.
 *
 *  **You should never face a blank box.** Nobody knows what an assistant can do
 *  until they have failed at it twice. The empty state is a set of real
 *  questions you click, drawn from the tools actually registered — so the first
 *  interaction succeeds and teaches the second.
 *
 *  **A write is never silent.** Tools that change data return a proposal, and it
 *  lands here as a card with Apply and Cancel. Aimee says what it is about to
 *  do; the person says whether it happens. Same shape as Record Payments,
 *  because the office already trusts that one.
 *
 *  **Cost is visible while it is being spent**, not in a bill next month. Each
 *  answer carries what it cost and the header carries the running total against
 *  the month's budget. */

type Busy = "" | "thinking" | "listening" | "reading";

export default function Aimee() {
  const [caps, setCaps] = useState<AimeeCapabilities | null>(null);
  const [conversations, setConversations] = useState<AimeeConversation[]>([]);
  const [current, setCurrent] = useState<AimeeConversation | null>(null);
  const [messages, setMessages] = useState<AimeeMessage[]>([]);
  const [budget, setBudget] = useState<AimeeBudget | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<Busy>("");
  const [error, setError] = useState("");
  // The thread rail is a permanent column on desktop and a drawer on mobile
  // (see the 900px breakpoint in styles.css) — this only matters on the
  // latter, where it starts closed so a chat doesn't open behind it.
  const [threadsOpen, setThreadsOpen] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.aimeeCapabilities().then((c) => { setCaps(c); setBudget(c.budget); })
      .catch((e) => setError(e.message));
    api.aimeeConversations().then(setConversations).catch(() => {});
  }, []);

  // Follow the conversation as it grows. Nothing is more irritating than an
  // answer arriving below the fold.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, busy]);

  async function ensureConversation(): Promise<AimeeConversation> {
    if (current) return current;
    const created = await api.aimeeNewConversation();
    setCurrent(created);
    setMessages([]);
    setConversations((prev) => [created, ...prev]);
    return created;
  }

  async function openConversation(id: number) {
    setError("");
    const c = await api.aimeeConversation(id);
    setCurrent(c);
    setMessages(c.messages ?? []);
    setThreadsOpen(false); // no-op on desktop; closes the drawer on mobile
  }

  function newChat() {
    setCurrent(null);
    setMessages([]);
    setText("");
    setError("");
    setThreadsOpen(false);
  }

  async function deleteThread(id: number) {
    await api.aimeeDeleteConversation(id);
    setConversations((prev) => prev.filter((c) => c.id !== id));
    // Deleting the open conversation has to leave the chat in a state that
    // still makes sense — showing messages that belong to a thread which no
    // longer exists would let someone reply into nothing.
    if (current?.id === id) newChat();
  }

  /** Runs a turn and folds the result in. One place, so text, voice and image
   *  all behave identically once their input has been captured. */
  async function send(
    run: (id: number) => Promise<Awaited<ReturnType<typeof api.aimeeAsk>>>,
    optimistic?: AimeeMessage,
    state: Busy = "thinking",
  ) {
    setError("");
    setBusy(state);
    try {
      const conversation = await ensureConversation();
      if (optimistic) setMessages((prev) => [...prev, optimistic]);
      const turn = await run(conversation.id);
      // Replace the optimistic echo with the server's own record, so ids are
      // real and a proposal can be acted on.
      setMessages((prev) => [
        ...prev.filter((m) => m.id !== optimistic?.id),
        ...turn.messages,
      ]);
      setCurrent(turn.conversation);
      setBudget(turn.budget);
      setConversations((prev) => {
        const rest = prev.filter((c) => c.id !== turn.conversation.id);
        return [turn.conversation, ...rest];
      });
      // Deliberately NOT surfacing turn.error here. A turn that failed still
      // becomes an assistant message carrying the reason, so it is already on
      // screen and already in the transcript. Raising the banner as well
      // printed the same sentence twice. The banner is for failures that never
      // became a message at all — see the catch below.
    } catch (e) {
      setError((e as Error).message);
      if (optimistic) setMessages((prev) => prev.filter((m) => m.id !== optimistic.id));
    } finally {
      setBusy("");
    }
  }

  function ask(question: string) {
    const q = question.trim();
    if (!q || busy) return;
    setText("");
    send(
      (id) => api.aimeeAsk(id, q),
      {
        id: -Date.now(), role: "user", content: q, tool_name: null,
        tool_ok: null, tool_result: null, proposal: null, proposal_status: null,
        cost_usd: 0, attachment_kind: null, attachment_name: null,
        created_at: null,
      },
    );
  }

  async function decide(message: AimeeMessage, approve: boolean) {
    try {
      const outcome = await api.aimeeDecide(message.id, approve);
      setMessages((prev) =>
        prev.map((m) =>
          m.id === message.id
            ? { ...m, proposal_status: outcome.status as AimeeMessage["proposal_status"] }
            : m
        )
      );
      if (!outcome.ok && approve) setError(outcome.summary);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const empty = messages.length === 0;

  const over = budget?.remaining_usd != null && budget.remaining_usd < 0;

  return (
    <div className="aimee">
      <aside className={"aimee-side" + (threadsOpen ? " open" : "")}>
        <button className="btn primary aimee-new" onClick={newChat}>
          ✚ New chat
        </button>
        <div className="aimee-threads">
          {conversations.map((c) => (
            <div
              key={c.id}
              className={"aimee-thread" + (current?.id === c.id ? " active" : "")}
            >
              <button
                className="aimee-thread-open"
                onClick={() => openConversation(c.id)}
                title={c.title}
              >
                <span className="aimee-thread-title">{c.title || "New chat"}</span>
              </button>
              <div className="aimee-thread-row">
                <span className="muted aimee-thread-cost">
                  {c.cost_usd > 0 ? `$${c.cost_usd.toFixed(3)}` : ""}
                </span>
                <span className="aimee-thread-delete">
                  <DeleteButton
                    title="Delete this chat"
                    onDelete={() => deleteThread(c.id)}
                  />
                </span>
              </div>
            </div>
          ))}
          {conversations.length === 0 && (
            <p className="muted" style={{ fontSize: 12, padding: "0 4px" }}>
              Your chats will appear here.
            </p>
          )}
        </div>
      </aside>
      {/* Adjacent-sibling in CSS: showing this on top of everything else only
          when .aimee-side carries .open is what makes tapping outside the
          drawer close it, without a separate piece of state to keep in sync. */}
      <div className="aimee-threads-backdrop" onClick={() => setThreadsOpen(false)} />

      <section className="aimee-main">
        <header className="aimee-head">
          <button
            className="btn aimee-threads-toggle"
            onClick={() => setThreadsOpen(true)}
            aria-label="Show past chats"
          >
            ☰ Chats
          </button>
          <div>
            <div className="aimee-name">
              <span className="aimee-dot" /> Aimee
            </div>
            <div className="muted" style={{ fontSize: 12 }}>
              Events, sales, clients and cash — ask in your own words
            </div>
          </div>
          <div className="aimee-cost" title="What this conversation has cost, and what is left of this month's AI budget">
            {current && current.cost_usd > 0 && (
              <span className="aimee-cost-chat">
                this chat ${current.cost_usd.toFixed(3)}
              </span>
            )}
            {budget?.remaining_usd != null && (
              <span className={"aimee-budget-pill" + (over ? " over" : "")}>
                {over
                  ? `${money(Math.abs(budget.remaining_usd))} over budget`
                  : `${money(budget.remaining_usd)} left`} this month
              </span>
            )}
          </div>
        </header>

        <div className="aimee-log">
          {empty && (
            <div className="aimee-empty">
              <div className="aimee-empty-title">What can I help with?</div>
              <p className="muted">
                Ask anything about your events, sales or clients. Click one to
                start, or type your own — you can also speak or send a photo.
              </p>
              <div className="aimee-suggestions">
                {(caps?.suggestions ?? []).map((s) => (
                  <button key={s.label} className="aimee-chip" onClick={() => ask(s.text)}>
                    <span className="aimee-chip-icon">{s.icon}</span>
                    <span>{s.label}</span>
                  </button>
                ))}
              </div>
              {caps && (
                <details className="aimee-what">
                  <summary>What Aimee can reach ({caps.tools.length})</summary>
                  <ul>
                    {caps.tools.map((t) => (
                      <li key={t.name}>
                        <strong>{t.name.replace(/_/g, " ")}</strong>
                        {t.kind === "write" && (
                          <span className="aimee-write-tag">asks before saving</span>
                        )}
                        <div className="muted">{t.description}</div>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          )}

          {messages.map((m) => (
            <Bubble key={m.id} message={m} onDecide={decide} />
          ))}

          {busy && (
            <div className="aimee-msg assistant">
              <div className="aimee-bubble aimee-typing">
                <span /><span /><span />
                <em className="muted">
                  {busy === "listening" ? "Listening back…"
                    : busy === "reading" ? "Looking at the image…"
                    : "Thinking…"}
                </em>
              </div>
            </div>
          )}
          <div ref={endRef} />
        </div>

        {error && <div className="aimee-error">⚠ {error}</div>}

        <Composer
          text={text}
          setText={setText}
          disabled={!!busy}
          onSend={() => ask(text)}
          onVoice={(file) =>
            send((id) => api.aimeeAskVoice(id, file), undefined, "listening")}
          onImage={(file, caption) =>
            send((id) => api.aimeeAskImage(id, file, caption), undefined, "reading")}
        />
      </section>
    </div>
  );
}

/** One turn on screen. Tool rows render as a quiet step rather than a bubble —
 *  they are evidence of work, not part of the conversation. */
function Bubble({
  message,
  onDecide,
}: {
  message: AimeeMessage;
  onDecide: (m: AimeeMessage, approve: boolean) => void;
}) {
  if (message.role === "tool") {
    // Anything visual comes from the TOOL's own result, never from the
    // assistant's prose. Handed a Street View URL, the model rewrote it into
    // an invented maps.googleapis.com link with `key=YOUR_API_KEY` and printed
    // it as raw markdown. The picture is data, so the data renders it.
    const display = message.tool_result?._display as
      | { kind?: string; url?: string; alt?: string }
      | undefined;
    return (
      <>
        <div className="aimee-step">
          <span className={"aimee-step-dot" + (message.tool_ok ? " ok" : " bad")} />
          {message.tool_ok ? "Checked" : "Couldn't check"}{" "}
          <strong>{(message.tool_name || "").replace(/_/g, " ")}</strong>
          {!message.tool_ok && message.tool_result?.error != null && (
            <span className="muted"> — {String(message.tool_result.error)}</span>
          )}
        </div>
        {display?.kind === "image" && display.url && (
          <div className="aimee-msg assistant">
            <img
              className="aimee-photo"
              src={display.url}
              alt={display.alt || "Image"}
              loading="lazy"
            />
          </div>
        )}
      </>
    );
  }

  const isUser = message.role === "user";
  const sentAt = message.created_at ? new Date(message.created_at).toLocaleString() : "";
  return (
    <div className={"aimee-msg " + (isUser ? "user" : "assistant")}>
      <div className="aimee-bubble" title={sentAt || undefined}>
        {message.attachment_kind && (
          <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>
            {message.attachment_kind === "voice" ? "🎙 voice" : "📷 image"}
            {message.attachment_name ? ` · ${message.attachment_name}` : ""}
          </div>
        )}
        <Rendered text={message.content} />
        {message.proposal && (
          <Proposal message={message} onDecide={onDecide} />
        )}
      </div>
      {!isUser && message.cost_usd > 0 && (
        <div className="aimee-msg-cost muted">${message.cost_usd.toFixed(4)}</div>
      )}
    </div>
  );
}

/** A change waiting on a person.
 *
 *  Deliberately not a line of prose with a link. The figures are laid out, the
 *  previous value is shown when there is one, and the two buttons say exactly
 *  what they do — because this is the moment somebody either updates the ledger
 *  or does not. */
function Proposal({
  message,
  onDecide,
}: {
  message: AimeeMessage;
  onDecide: (m: AimeeMessage, approve: boolean) => void;
}) {
  const p = message.proposal!;
  const status = message.proposal_status;

  if (status === "applied") {
    return <div className="aimee-proposal done">✅ Saved — {String(p.summary ?? "")}</div>;
  }
  if (status === "cancelled") {
    return <div className="aimee-proposal cancelled">Cancelled — nothing was saved.</div>;
  }

  return (
    <div className="aimee-proposal">
      <div className="aimee-proposal-title">{String(p.summary ?? "Confirm this change")}</div>
      {p.previous_cash != null && (
        <div className="aimee-proposal-rows">
          <div>
            <span className="muted">Now</span>
            <strong>{money(Number(p.previous_cash))}</strong>
          </div>
          <div className="aimee-arrow">→</div>
          <div>
            <span className="muted">After</span>
            <strong>{money(Number(p.new_cash))}</strong>
          </div>
        </div>
      )}
      {p.replaces_existing === true && (
        <p className="muted" style={{ fontSize: 12, margin: "6px 0 0" }}>
          This replaces a figure already recorded.
        </p>
      )}
      <div className="aimee-proposal-actions">
        <button className="btn primary" onClick={() => onDecide(message, true)}>
          Apply
        </button>
        <button className="btn" onClick={() => onDecide(message, false)}>
          Cancel
        </button>
      </div>
    </div>
  );
}

/** Markdown-ish rendering, done by hand.
 *
 *  Tables, bold, code and lists cover everything the prompt asks Aimee to
 *  produce, and hand-rolling them avoids shipping a markdown library — and,
 *  more to the point, avoids ever setting innerHTML on model output. Every
 *  branch below builds React elements from text, so a reply containing a script
 *  tag renders as the words of a script tag. */
function Rendered({ text }: { text: string }) {
  if (!text) return null;
  const blocks: JSX.Element[] = [];
  const lines = text.split("\n");
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Table: a header row, a separator, then body rows.
    if (line.trim().startsWith("|") && (lines[i + 1] || "").includes("---")) {
      const rows: string[][] = [];
      const head = splitRow(line);
      i += 2;
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        rows.push(splitRow(lines[i]));
        i++;
      }
      blocks.push(
        <div className="table-wrap" key={blocks.length} style={{ margin: "8px 0" }}>
          <table>
            <thead>
              <tr>{head.map((h, n) => <th key={n}><Inline text={h} /></th>)}</tr>
            </thead>
            <tbody>
              {rows.map((r, n) => (
                <tr key={n}>{r.map((c, k) => <td key={k}><Inline text={c} /></td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      );
      continue;
    }

    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ""));
        i++;
      }
      blocks.push(
        <ul key={blocks.length} style={{ margin: "6px 0", paddingLeft: 20 }}>
          {items.map((t, n) => <li key={n}><Inline text={t} /></li>)}
        </ul>
      );
      continue;
    }

    if (!line.trim()) { i++; continue; }

    const para: string[] = [];
    while (i < lines.length && lines[i].trim() &&
           !lines[i].trim().startsWith("|") && !/^\s*[-*]\s+/.test(lines[i])) {
      para.push(lines[i]);
      i++;
    }
    blocks.push(
      <p key={blocks.length} style={{ margin: "0 0 8px" }}>
        <Inline text={para.join(" ")} />
      </p>
    );
  }
  return <>{blocks}</>;
}

function splitRow(line: string): string[] {
  return line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
}

/** **bold** and `code`, as elements rather than markup. */
function Inline({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return (
    <>
      {parts.map((part, n) => {
        if (part.startsWith("**") && part.endsWith("**")) {
          return <strong key={n}>{part.slice(2, -2)}</strong>;
        }
        if (part.startsWith("`") && part.endsWith("`")) {
          return <code key={n}>{part.slice(1, -1)}</code>;
        }
        return <span key={n}>{part}</span>;
      })}
    </>
  );
}

/** The input row: type, speak, or send a picture. */
function Composer({
  text, setText, disabled, onSend, onVoice, onImage,
}: {
  text: string;
  setText: (v: string) => void;
  disabled: boolean;
  onSend: () => void;
  onVoice: (file: File) => void;
  onImage: (file: File, caption: string) => void;
}) {
  const [recording, setRecording] = useState(false);
  const [micError, setMicError] = useState("");
  const recorder = useRef<{ rec: MediaRecorder; stream: MediaStream } | null>(null);
  const imageRef = useRef<HTMLInputElement>(null);

  const canRecord =
    typeof window !== "undefined" &&
    window.isSecureContext &&
    !!navigator.mediaDevices?.getUserMedia;

  async function startRecording() {
    setMicError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chunks: BlobPart[] = [];
      const rec = new MediaRecorder(stream);
      rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        recorder.current = null;
        setRecording(false);
        const type = rec.mimeType || "audio/webm";
        const blob = new Blob(chunks, { type });
        if (blob.size) {
          // The extension declares the format to the transcriber — Chrome gives
          // webm, Safari mp4, and a fixed name makes one of them unreadable.
          const name = type.includes("mp4") ? "question.mp4" : "question.webm";
          onVoice(new File([blob], name, { type }));
        }
      };
      recorder.current = { rec, stream };
      rec.start();
      setRecording(true);
    } catch (e) {
      setMicError(
        (e as Error)?.name === "NotAllowedError"
          ? "Microphone blocked — allow it in the address bar."
          : "Couldn't start recording."
      );
    }
  }

  // A recorder still holding the microphone after this row unmounts is a live
  // mic with no UI attached.
  useEffect(() => () => {
    recorder.current?.rec.stop();
    recorder.current?.stream.getTracks().forEach((t) => t.stop());
  }, []);

  return (
    <div className="aimee-composer">
      <input
        ref={imageRef}
        type="file"
        accept="image/*"
        capture="environment"
        style={{ display: "none" }}
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onImage(file, text.trim());
          setText("");
          e.target.value = "";
        }}
      />
      <button
        className="aimee-icon-btn"
        title="Send a photo"
        disabled={disabled}
        onClick={() => imageRef.current?.click()}
      >
        📷
      </button>

      <textarea
        className="aimee-input"
        placeholder="Ask Aimee anything…"
        value={text}
        disabled={disabled}
        rows={1}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          // Enter sends; Shift+Enter is a new line. The chat convention.
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            onSend();
          }
        }}
      />

      {canRecord && (
        <button
          className={"aimee-icon-btn" + (recording ? " recording" : "")}
          title={recording ? "Stop and send" : "Ask by voice"}
          disabled={disabled && !recording}
          onClick={() => (recording ? recorder.current?.rec.stop() : startRecording())}
        >
          {recording ? "⏹" : "🎙"}
        </button>
      )}

      <button
        className="btn primary aimee-send"
        disabled={disabled || !text.trim()}
        onClick={onSend}
      >
        Send
      </button>

      {micError && <div className="aimee-error">{micError}</div>}
    </div>
  );
}
