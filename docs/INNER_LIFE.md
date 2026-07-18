# Inner life — thinking, a moving face, and self-maintenance

[OUTPUT_CHAIN.md](OUTPUT_CHAIN.md) §4 established that some behaviours fire
without speaking, and that a skin should show a lamp instead of a voice. This
chapter is what happens *behind* the lamp — three patterns that together make
idle time real: the **silent thinking channel** (§1–§4), the **expression
direct push** that lets the face follow the mood between turns (§5), and the
**self-correction round** that keeps the voice from drifting (§6).

> Visible, never audible: the user sees that she's thinking, meets the person
> the thought left behind — and never hears the thought itself.

---

## 1. The library's half: `IntrospectSource`

The scheduler ships a dedicated source for this
(`companion/sources_ref.py`), and its gating is where the design lives:

| property | value | why |
|---|---|---|
| kind / payload | `"introspect"`, `""` | an empty payload marks a silent kind — the dispatcher runs it internally, nothing routes to the voice |
| quota | **does not spend** the daily proactive quota | thinking isn't talking; a rich inner life shouldn't ration the outer one |
| solitude gate | `solitude_min_s` (default 1800 s) of user absence | introspection happens *alone* — never mid-conversation, never competing with the user |
| cadence | once per configured time window per day, plus `introspect_gap_s` between fires | rare by construction; inner life is a rhythm, not a loop |
| failure semantics | anti-spam markers are set **on delivery**, not on proposal | a failed introspection this tick is retried rather than silently skipped |

The dispatcher contract (`companion/dispatch.py`) receives
`dispatch("introspect", "")` and must not raise — a failure here must not
kill the heartbeat. From that call onward, everything is the app's.

## 2. The app's half: a full turn, fully swallowed

The pattern that makes the lamp honest — run the *complete* turn machinery
with the output sunk:

```text
scheduler fires "introspect"
  │
  ├─ broadcast status: thinking          ← the ONLY thing the user perceives
  │    (frontend lamp / idle animation on)
  │
  ├─ run a real turn:
  │    static persona prefix             (unchanged, cached)
  │    felt block                        (engine.render(), as ever)
  │    an introspection prompt           (in place of a user message —
  │                                       "sit with the last few days…")
  │    → the model genuinely thinks; full generation
  │
  ├─ swallow the output:
  │    no TTS, no chat message — the text goes to a sink
  │    persist the monologue             (see §3)
  │    tick the engine on the exchange   (appraisal runs as normal)
  │
  └─ broadcast status: idle              (lamp off)
```

Three properties are load-bearing:

* **It is a real turn.** Same persona, same felt block, same appraisal
  afterwards. Cheap fakes (a canned "thinking…" with no model call) make the
  lamp theatre; the point is that state at the end differs because thought
  happened.
* **The output is swallowed, not suppressed.** The turn runs to completion
  and the text is kept — it simply never routes to a voice. A companion who
  audibly talks to herself every half hour is spam; one who never thinks is
  hollow. Swallowing is the middle.
* **Only status crosses to the skin.** The frontend gets a boolean-grade
  signal (thinking / not), never the monologue text. Same privacy shape as
  every surface rule in [INTEGRATION.md](INTEGRATION.md) §7.

## 3. Where the thought goes

The swallowed text has three destinations, in increasing visibility:

1. **The monologue lands on disk.** Append it to a diary file, or store
   distilled lines in `Canon` tagged as introspection — so later recall can
   distinguish *things I thought* from *things we said*. (The dispatcher
   docstring's "write a diary entry" is this, and the `"diary"` kind is a
   natural sibling behaviour.)
2. **The state moves.** The engine ticks on the introspection exchange like
   any other — a heavy realization actually shifts the bars, a settling one
   actually settles them. This is the mechanism by which idle hours change
   her: state at evening differs *because of* what she thought at noon.
3. **It seeps into the next real turn.** No announcement, no "while you were
   away I reflected…" — the changed state simply renders into the next felt
   block, and the mood the user meets is the mood the thinking left. If the
   monologue produced something worth *saying*, the app can queue it as a
   pending topic ([INTEGRATION.md](INTEGRATION.md) §4's proactive path) and
   let the ordinary gates decide when.

## 4. Cost, honestly

An introspection is a full model call. That is the price of an inner life
that's real rather than performed, and the source's gates (solitude, one per
window, minimum gap) exist precisely to keep the spend rare — a handful of
turns a day, all during idle time that would otherwise be dead. Budget
version: keep the lamp wired to the *scheduler fire* and make the model call
conditional — the rhythm stays visible even when a given fire is skipped;
just accept that a skipped fire moves nothing (§2's honesty note applies).

## 5. The face that moves on its own — expression direct push

[OUTPUT_CHAIN.md](OUTPUT_CHAIN.md) §1's face channel fires on turns — the
stock loop calls `expression_signal(prev, new)` after `say()` and after
proactive turns (`companion/app.py`, `_express_and_speak`). But state also
moves *between* turns: decay, aftertaste settling, a dream's residue, an
introspection's aftermath. The direct-push pattern extends the same three
seams to plain heartbeat ticks, so the face follows the mood while nobody is
talking:

```text
heartbeat tick ──► engine state drifts (no turn, no tokens)
   │
   ├─ label = expression_signal(prev, new)     ← same pure function
   ├─ if label != last_pushed_label:           ← dedupe, or the face flickers
   │     token = frontend.label_to_token(label)
   │     await frontend.push_expression(token) ← throttle lives here
   └─ remember last_pushed_label
```

The pieces are already shaped for it: `expression_signal` is a pure function
of (prev, new) — it doesn't care whether a turn caused the change — and
`push_expression`'s contract says *"Throttle here if the avatar needs a min
switch interval"* (`companion/frontend.py`), so the adapter owns the minimum
gap (a few hundred milliseconds up to ~a second reads naturally on an
avatar; per-frame pushes read as twitching).

Three properties worth protecting:

* **Zero tokens.** The whole path is a state readout — no LLM, no TTS. This
  is the cheapest aliveness in the system: an avatar whose face drifts with
  a genuinely drifting state, for free.
* **Labels only cross.** The skin still receives the rendered label, never
  bars or raw state — the same privacy shape as every other surface.
* **Dedupe before throttle.** The throttle prevents *fast* changes; the
  dedupe prevents *no* change re-firing. Both, or the face strobes on the
  tick cadence.

This is the visible half of §2's bargain: introspection changes the state in
silence, and the direct push is how the change reaches the face *before* the
next conversation — the user glances over and she looks different, because
she is.

## 6. The self-correction round — self-maintenance, slow half

[PROMPT_STACK.md](PROMPT_STACK.md) §3's forget probe is the fast half of
self-maintenance: per-turn, mechanical, free — a missing micro-act triggers a
one-turn reminder. Its blind spot is drift that never misses the probe:
replies that keep the tag but slowly stop sounding like her. The slow half is
a **self-correction round** — a solitude-gated silent fire (the
`IntrospectSource` family; a review payload on the same gates) that runs a
swallowed turn over her own recent output:

```text
prompt:   persona core
        + the last N replies, verbatim
        + "which of these don't sound like me — and what drifted?"
output:   swallowed (never spoken, never shown)
sinks:    a correction note in the diary / Canon, introspection-tagged
        + optionally: one self-authored nudge line, ridden under the felt
          block for the next few turns ("keep the edges shorter — I've
          been rambling"), then expired
```

The rules that keep it healthy:

* **Voice, not facts.** The round judges form — cadence, phrasing, whether
  the persona's edges survived — and never rewrites history or second-guesses
  factual answers. Fact hygiene belongs to memory's own lifecycle
  ([MEMORY_TOOLS.md](MEMORY_TOOLS.md)).
* **Notes-to-self, never apologies.** Nothing reviewed was wrong *at* the
  user; the output is private. A companion who audibly relitigates her own
  messages is worse than one who drifts.
* **Nudges expire.** The correction line rides a few turns and dies.
  Persistent self-criticism turns into a permanent instruction — exactly
  what the description-not-instruction rule exists to prevent, aimed inward.

Probe and round compose into one design: a cheap reflex that catches the
symptom the moment it shows, and a periodic checkup that catches what the
reflex can't see — both silent, both feeding the same seams (a reminder
line, the felt block, Canon).

---

*See also:* [OUTPUT_CHAIN.md](OUTPUT_CHAIN.md) §4 — the silent-behaviour
dispatch path this chapter fills in · [PROMPT_STACK.md](PROMPT_STACK.md) §3 —
the forget probe, this chapter's fast reflex ·
[INTEGRATION.md](INTEGRATION.md) §3 — the heartbeat that carries the
scheduler these fires ride on.
