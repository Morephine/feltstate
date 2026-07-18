# Bridge etiquette — being a person over a chat platform

At home the companion has a face, a voice, and timing. Over a bridge — Discord,
Telegram, any chat surface — she has *text and the platform's affordances*.
Reactions, typing indicators, and attachments stop being decorations: they are
her body language now. This page is the etiquette that keeps a text bridge
feeling like the same person, plus the emergency lane every bridge needs.

(This is an app-side protocol chapter: the library's parts of the story are the
narration/receipt principles from [AGENT_WORK_UX.md](AGENT_WORK_UX.md) and the
privacy boundary from [INTEGRATION.md](INTEGRATION.md) §7 — both apply
verbatim here.)

---

## 1. Receipts — a small, fixed emoji vocabulary

A reaction is a receipt, not a mood sticker. Keep the vocabulary tiny and
constant so each mark stays legible:

| receipt | meaning | when |
|---|---|---|
| 🤖 (or a "working" mark) | *your message is being worked on* | attached when a long/agentic turn starts; stays until it ends |
| ✅ | *done — the work your message asked for finished* | replaces the working mark |
| 😶 | *I read this and chose not to answer* | deliberate silence — distinct from crash-silence, which is the whole point |
| 🈵 | *queue full, this one was dropped* | back-pressure made visible instead of silent loss |

Four rules make receipts trustworthy:

1. **Anchor on the user's message.** The receipt rides the message it answers —
   visible even when the reply comes minutes later.
2. **One receipt per state transition**, not per internal event. Ten tool calls
   are still one 🤖. (The narration throttle's logic, applied to reactions.)
3. **Never churn.** Adding/removing reactions in a loop hits platform rate
   limits and reads as glitching. A receipt changes at most twice: appear,
   resolve.
4. **Deliberate silence gets a mark.** An empty reply with no receipt is
   indistinguishable from a hang. 😶 turns "nothing" into a statement.

## 2. The typing indicator — the bridge's thinking lamp

Turn it on when a turn starts, off when the reply (or failure line) lands.
It is the text-surface equivalent of the silent-thinking lamp
([OUTPUT_CHAIN.md](OUTPUT_CHAIN.md) §4): visible inner life during the gap.
Two cautions: never leave it dangling after a failure (a stuck "typing…" is
the bridge's frozen face), and don't flash it for sub-second replies — it
reads as theatre.

## 3. Attachments — things crossing the bridge

**Inbound**: persist the file locally first, then perceive it (vision, parsing)
from the local copy, then reply to it. Never re-upload or forward a user's
attachment anywhere — it entered *this* conversation, it stays there.

**Outbound**: work products go as attachments with a one-line, in-character
caption — the caption is speech, the file is the artifact; don't paste file
contents into chat when a file is the honest shape. Platform size caps get an
explicit fallback (a paste service, a trimmed version) chosen *by the app*,
announced in one line, never silently truncated.

## 4. The emergency lane — control that works when the brain is down

Every bridge needs a **plain-text command prefix** (`!stop`, `!clear`,
`!status`, `!restart`) parsed by the *bridge process itself* — never routed
through the model. Design rules:

* **It must work when everything else is dead.** That is its entire reason to
  exist: gateway wedged, model stalled, queue jammed — the `!` lane still
  parses, because it is string-matching in the bridge loop, nothing more.
* **Terse and mechanical on purpose.** This is the service hatch; replies are
  one-line status strings, not conversation. The deliberate tone-break marks
  the boundary between talking *to her* and operating *her machinery* — the
  one place out-of-character is correct.
* **De-escalate or configure — never grant.** The lane may stop, clear,
  restart, report status, and flip neutral config (a model-tier switch, a
  verbosity level). What it must never do is *grant*: permissions,
  capabilities, spending all go through the full, gated path. A hatch that
  cannot escalate is a hatch you never regret having.
* **Commands are not conversation.** They don't enter history, don't tick the
  engine, don't leave receipts beyond their own status line.

## 5. What never crosses the bridge

Restating [INTEGRATION.md](INTEGRATION.md) §7 because bridges are where it
gets violated: the bridge surface receives *rendered speech and receipts* —
never raw stores, never state files, never the operator-side error text
([FAILURE_IN_CHARACTER.md](FAILURE_IN_CHARACTER.md) keeps the two audiences
apart). If several people share the surface, relationship state is keyed per
speaker and one person's context never colours another's reply.
