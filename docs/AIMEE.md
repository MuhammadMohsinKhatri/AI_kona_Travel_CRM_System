# Aimee — chat assistant architecture

A design to agree before any code is written. It covers what the fourteen tools
actually are, what has to be built versus what already exists, and the shape of
the agent itself.

The n8n original — *Aimee - Telegram Responder V4* — is 96 nodes, 5 agents and
roughly 40 tools in one workflow. It works, and it is at the limit of what
anyone can reason about. The point of the rewrite is not to reproduce that in
Python; it is to keep the behaviour and lose the sprawl.

---

## 1. The finding that changes the estimate

**Every KonaOS endpoint the dead Railway API served, this system already
proxies.** The tools pointed at `konaoscrmsapis-production.up.railway.app`,
which is gone. We do not need to rebuild it.

| Railway endpoint (dead) | Already live here |
|---|---|
| `GET /events` | `GET /api/konaos/events` |
| `GET /events/{id}` | `GET /api/konaos/events/{event_id}` |
| `PUT /events/{id}` | `PUT /api/konaos/events/{event_id}` |
| `GET /clients` | `GET /api/konaos/clients` |
| `GET /clients/{id}` | `GET /api/konaos/clients/{client_id}` |
| `POST /reports/sales-data` | `POST /api/konaos/reports/sales-data` |
| `POST /reports/client-ranking` | `POST /api/konaos/reports/client-ranking` |
| `GET /staff/availability` | `GET /api/konaos/staff/availability` |

Zero new KonaOS work. One tool — *update cash amount* — already calls this
system directly (`/api/financials/by-event/{id}/cash`), so the precedent exists
and the pattern is proven.

**What genuinely does not exist yet**, and is the real build:

| Integration | Used by | Notes |
|---|---|---|
| **Samsara** | truck location, fuel levels, ETA | `api.samsara.com/fleet/*`. New adapter. |
| **Google Maps** | routes, ETA, street view, place search | geocode, directions, places, streetview |
| **Square timecards** | employee clock in/out | We already talk to Square for orders — extends the existing client |

Three adapters, all read-only, all following `app/integrations/base.py` so they
mock cleanly in tests.

---

## 2. What the fourteen things actually are

Sorting them by what they *do* rather than what they were called matters,
because two of them are not tools at all.

### Read tools — 11

No consequence, safe to call freely, safe to retry.

| Tool | Source |
|---|---|
| Sales data report | KonaOS (proxied) |
| Client ranking report | KonaOS (proxied) |
| Fetch events for calendar | KonaOS (proxied) |
| Truck location | Samsara |
| Truck gas levels | Samsara |
| ETA for a truck | Samsara + Google |
| Calculate departure time | Google |
| Calculate travel and arrival time | Google |
| Create truck route | Google |
| Street view image | Google |
| Employee clock in/out times | Square |

### Write tool — 1

| Tool | Effect |
|---|---|
| Update cash amount | Writes cash to the event in KonaOS **and** the local ledger |

### Not tools at all — 2

**Daily gas-level alert** and **employee clock in/out alert** were separate n8n
workflows on a schedule and a webhook, pushing to Telegram. They are not things
Aimee calls; they are things that call the user.

In the new system they are a **Celery beat task** and a **webhook receiver**,
writing rows to a notifications table the UI surfaces. Putting them in the
agent's toolbox would be a category error — and would mean the agent could
"send an alert" on request, which is not what an alert is.

This is worth stating plainly because it removes two items from the agent's
surface area for free.

---

## 3. The architecture question: one agent, or subagents?

**Recommendation: one agent. No subagents, for now.**

The instinct to reach for subagents comes from the n8n version, where five
agents were a symptom rather than a design — nodes accreted until a second agent
was easier than untangling the first. Twelve well-described tools is not a lot;
current models select from that comfortably.

What subagents would cost here:

- **Latency.** Every hop is another model round-trip. A question answered
  directly in two seconds takes six through a router.
- **Cost.** The routing call is billed, and the sub-agent re-reads context.
- **Failure modes.** A router that picks the wrong sub-agent fails in a way that
  is much harder to diagnose than a tool that returns an error.
- **Debuggability.** One transcript beats three.

What actually delivers the isolation being asked for — *"changes in one thing
don't affect another"* — is not agent topology. It is **module boundaries in
code**:

```
app/aimee/
  agent.py          the loop, the prompt, the model call
  registry.py       tool discovery — nothing else knows the list
  tools/
    fleet.py        Samsara: location, fuel
    routing.py      Google: routes, ETA, departure, street view
    reports.py      KonaOS: sales, client ranking
    calendar.py     KonaOS: events
    staff.py        Square: clock in/out
    finance.py      the one write: cash
```

Each tool module is self-contained, declares its own schema, and catches its own
errors. Editing `fleet.py` cannot break `reports.py`, because they share nothing
but the registry interface. That is the isolation — and it costs no latency.

**When a subagent WOULD earn its place:** a task needing several steps of
reasoning within one domain, where the intermediate work would pollute the main
conversation. *"Plan tomorrow's routes for all five trucks"* is the plausible
candidate — it wants a dozen tool calls and produces one answer. Add it when a
real request demands it, not in advance.

---

## 4. Read and write — separate them, but not by topology

**Yes, separate them.** Not with different agents — with different *treatment*
of the result.

Every tool declares its kind:

```python
@tool(kind="read")     # runs immediately, result goes to the model
@tool(kind="write")    # produces a PROPOSAL the user confirms
```

A write tool does not write. It returns a described change, and the chat renders
it as a card with **Apply** and **Cancel** — the same review-then-apply pattern
already proven in Record Payments, which the office has used and understands:

> **Update cash for Arbutus Food Truck (2026-07-29)**
> Cash collected: $63.00 → **$85.00**
> [Apply] [Cancel]

Three reasons this is the right seam:

1. **A model's confident mistake becomes a two-second cancel** instead of a
   wrong figure in the ledger, discovered whenever someone next looks.
2. **Reads can be retried freely.** If the agent calls "get truck location"
   twice, nothing happens twice.
3. **It matches what already exists.** The same shape, the same audit trail,
   the same mental model for the office.

When a second write tool arrives — driver notes, event creation — it inherits
this for free.

---

## 5. Conversation, context and history

| Concern | Approach |
|---|---|
| Storage | Postgres: `conversations`, `messages`, `tool_calls` |
| What the model sees | Rolling window of recent turns + a running summary of older ones |
| Tool results | Stored separately from displayed text — a 40 KB report does not go back into every subsequent prompt |
| Attachments | Voice and images stored on disk, referenced by id |

The n8n version used a `memoryBufferWindow` — the last N messages, nothing else.
That loses the thread on any long conversation. A rolling window plus a summary
keeps the cost flat while retaining "the truck we discussed earlier".

**Input modes:** text, voice, image — the three the UI must handle.

- **Voice** → the same `translations` endpoint the cash feature uses, so it
  arrives in English regardless of what was spoken. That fix is already made.
- **Image** → vision. A photo of a cheque should route to the existing check
  reader rather than a general description; a photo of anything else is
  described.

---

## 6. Cost and budget — reuse, don't rebuild

Already exists: [`app/core/ai_budget.py`](../backend/app/core/ai_budget.py),
with `get_budget`, `set_budget` and a combined call returning budget, spend and
remaining — read from **OpenAI's actual billing**, not a local tally.

What to add:

- token counts and cost **per message**, stored on the message row
- a **per-conversation total**, shown in the chat header
- the existing **monthly remaining** figure, shown alongside it

So the UI shows *"this conversation: $0.04 · this month: $12.60 of $50"* — one
new column and one existing function, not a new subsystem.

---

## 7. Errors — how one broken thing stays one broken thing

Every tool returns a structured result, never an exception:

```python
{"ok": False, "error": "Samsara did not respond", "retryable": True}
```

The agent sees the failure, tells the user which capability is unavailable, and
carries on answering what it can. A dead Samsara key means "I can't reach the
trucks right now" — not a failed chat.

Three rules that follow:

- **No tool may raise into the agent loop.** Each catches its own.
- **Timeouts on every external call.** A hung Samsara request must not hang the
  conversation.
- **The model is told what failed**, so it can say so rather than inventing a
  truck location. This matters more than it sounds: a tool that silently returns
  nothing invites a plausible fabrication.

---

## 8. What I would build, in order

1. **Tool registry + three read tools** (truck location, gas, events) — proves
   the whole path end to end with the smallest surface.
2. **Chat UI** — text only, streaming, history, cost display.
3. **Remaining read tools** — reports, routing, street view, Square.
4. **Voice and image input.**
5. **The one write tool**, with the propose-and-confirm card.
6. **Notifications** — gas beat task, clock in/out webhook, and the panel that
   shows them.

Each step is usable on its own. Nothing needs the next step to be worth having.

---

## 9. Decisions I need from you

1. **Single agent — agreed?** My recommendation, with a subagent added only when
   a real task demands it. Say if you would rather start with the split.

2. **Write confirmation — Apply/Cancel card, or fully automatic?** I recommend
   the card, for the same reason cheques get one. It is a one-line change to
   flip later once you have watched it work.

3. **Which model?** `gpt-4o` handles vision and tool-calling well and is what the
   n8n version used. The classifier stays on `gpt-5-mini` regardless — that
   decision is settled and unrelated.

4. **Samsara and Google Maps credentials** — the n8n workflows hold working keys.
   Are those ours to reuse, or does Brett need to issue new ones?

5. **Who may talk to Aimee?** Any logged-in dashboard user, or a named subset?
   This decides whether the write tool needs its own permission check.
