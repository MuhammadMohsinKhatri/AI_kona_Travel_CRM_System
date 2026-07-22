import { useEffect, useState } from "react";
import { TelegramSettings, TelegramTestResult, api } from "../api/client";
import { Loading } from "../components/ui";

/** Telegram alert delivery, configured at runtime rather than in .env.
 *
 *  The bot token is a live credential — anyone holding it controls the bot —
 *  so it is write-only: the server never sends it back, and this page shows a
 *  mask once one is stored. Leaving the field untouched keeps the saved token,
 *  which is what lets someone add a chat id without re-entering it. */
export default function Settings() {
  const [cfg, setCfg] = useState<TelegramSettings | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [token, setToken] = useState("");
  const [tokenDirty, setTokenDirty] = useState(false);
  const [dashboardUrl, setDashboardUrl] = useState("");
  const [chatIds, setChatIds] = useState<string[]>([]);
  const [newChat, setNewChat] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");
  const [test, setTest] = useState<TelegramTestResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => { load(); }, []);

  function apply(c: TelegramSettings) {
    setCfg(c);
    setEnabled(c.enabled);
    setDashboardUrl(c.dashboard_url);
    setChatIds(c.chat_ids);
    setToken(c.bot_token);      // the mask, or ""
    setTokenDirty(false);
  }

  function load() {
    api.telegramSettings().then(apply).catch((e: any) =>
      setError(e?.message || "Couldn't load settings")
    );
  }

  function addChat() {
    const id = newChat.trim();
    if (!id) return;
    if (chatIds.includes(id)) { setNewChat(""); return; }
    setChatIds([...chatIds, id]);
    setNewChat("");
  }

  async function save() {
    setSaving(true);
    setMsg("");
    setTest(null);
    try {
      const c = await api.saveTelegramSettings({
        enabled,
        chat_ids: chatIds,
        dashboard_url: dashboardUrl,
        // Only send the token if it was actually edited — otherwise the
        // stored one stands.
        ...(tokenDirty ? { bot_token: token } : {}),
      });
      apply(c);
      setMsg("Saved.");
    } catch (e: any) {
      setMsg(`Couldn't save: ${e?.message || "unknown error"}`);
    } finally {
      setSaving(false);
    }
  }

  async function sendTest() {
    setTesting(true);
    setTest(null);
    try {
      setTest(await api.testTelegram());
    } catch (e: any) {
      setTest({ ok: false, detail: e?.message || "Failed", sent: 0, failed: 0, errors: [] });
    } finally {
      setTesting(false);
    }
  }

  if (error) {
    return <div className="card" style={{ borderColor: "var(--crit)" }}>{error}</div>;
  }
  if (!cfg) return <Loading />;

  return (
    <>
      <h1 className="page-title">Settings</h1>
      <p className="page-sub">
        Where the system sends alerts. Everything else is configured by your developer.
      </p>

      <div className="card" style={{ marginBottom: 16, maxWidth: 760 }}>
        <div className="flex between" style={{ marginBottom: 4 }}>
          <h2 style={{ margin: 0, fontSize: 16 }}>Telegram alerts</h2>
          <span className={"badge " + (cfg.configured ? "green" : "gray")}>
            {cfg.configured ? "Active" : "Not set up"}
          </span>
        </div>
        <p className="muted" style={{ fontSize: 13, margin: "0 0 16px", lineHeight: 1.55 }}>
          Send every alert to one or more Telegram chats, with a link straight to the
          problem. Alerts always appear on the Needs Attention page — this only adds
          the push. If it isn't set up, nothing breaks; the message is simply skipped.
        </p>

        <label className="chk" style={{ marginBottom: 16 }}>
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          Send alerts to Telegram
        </label>

        <div className="field">
          <label htmlFor="tg-token">Bot token</label>
          <input
            id="tg-token"
            className="input"
            value={token}
            placeholder="123456:ABC-DEF…  (or paste the whole api.telegram.org URL)"
            onChange={(e) => { setToken(e.target.value); setTokenDirty(true); }}
            onFocus={() => { if (!tokenDirty && cfg.bot_token_set) { setToken(""); setTokenDirty(true); } }}
          />
          <div className="muted" style={{ fontSize: 12, marginTop: 5 }}>
            {cfg.bot_token_set && !tokenDirty
              ? "A token is saved. Click the field to replace it."
              : "From @BotFather in Telegram. Stored securely and never shown again after saving."}
          </div>
        </div>

        <div className="field">
          <label htmlFor="tg-url">Dashboard web address</label>
          <input
            id="tg-url"
            className="input"
            value={dashboardUrl}
            placeholder="https://ops.example.com"
            onChange={(e) => setDashboardUrl(e.target.value)}
          />
          <div className="muted" style={{ fontSize: 12, marginTop: 5 }}>
            Used to build the "Open this alert" link in each message. Without it the
            messages still send, just without a clickable link.
          </div>
        </div>

        <div className="field">
          <label>Chats to notify</label>
          <div className="muted" style={{ fontSize: 12, margin: "0 0 8px" }}>
            Add as many as you like — every alert goes to all of them. To find a chat id,
            message your bot, then open{" "}
            <code className="inline">api.telegram.org/bot&lt;TOKEN&gt;/getUpdates</code> and
            copy <code className="inline">chat.id</code>.
          </div>
          {chatIds.length === 0 && (
            <div className="muted" style={{ fontSize: 13, marginBottom: 8 }}>
              No chats yet — alerts will only appear on the Needs Attention page.
            </div>
          )}
          <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 8 }}>
            {chatIds.map((id) => (
              <div key={id} className="flex between" style={{
                background: "var(--surface-2)", borderRadius: 8, padding: "7px 12px",
              }}>
                <code style={{ fontSize: 13 }}>{id}</code>
                <button className="icon-btn btn" title="Remove this chat"
                  onClick={() => setChatIds(chatIds.filter((c) => c !== id))}>✕</button>
              </div>
            ))}
          </div>
          <div className="flex" style={{ gap: 8 }}>
            <input
              className="input"
              placeholder="Chat id, e.g. -1001234567890"
              value={newChat}
              onChange={(e) => setNewChat(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addChat(); } }}
            />
            <button className="btn" onClick={addChat} disabled={!newChat.trim()}>＋ Add</button>
          </div>
        </div>

        <div className="flex" style={{ gap: 10, marginTop: 18 }}>
          <button className="btn primary" onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save settings"}
          </button>
          <button className="btn" onClick={sendTest} disabled={testing || !cfg.configured}
            title={cfg.configured
              ? "Send a test message to every chat above"
              : "Save a token, at least one chat, and turn Telegram on first"}>
            {testing ? "Sending…" : "Send test message"}
          </button>
          {msg && <span className="muted" style={{ fontSize: 13 }}>{msg}</span>}
        </div>

        {test && (
          <div className={"status " + (test.ok ? "ok" : "bad")} style={{ marginTop: 14 }}>
            <strong>{test.detail}</strong>
            {test.errors.length > 0 && (
              <ul>{test.errors.map((e) => <li key={e}>{e}</li>)}</ul>
            )}
          </div>
        )}
      </div>

      <div className="card" style={{ maxWidth: 760 }}>
        <h2 style={{ margin: "0 0 8px", fontSize: 16 }}>What gets sent</h2>
        <p className="muted" style={{ fontSize: 13, margin: 0, lineHeight: 1.6 }}>
          Three kinds of alert are pushed, all to every chat above:
        </p>
        <ul className="muted" style={{ fontSize: 13, lineHeight: 1.7, marginBottom: 0 }}>
          <li><strong>Event problems</strong> — the nightly run couldn't work out a bill
            because something is missing from the event notes.</li>
          <li><strong>Waiting on cash</strong> — a minimum-guarantee event's cash still
            hasn't been recorded after 3 days, so its invoice can't be settled.</li>
          <li><strong>KonaOS connection</strong> — the session key expired and the
            automation can't reach KonaOS at all.</li>
        </ul>
      </div>
    </>
  );
}
