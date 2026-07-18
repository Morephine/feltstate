# Inner life — the silent thinking channel

[OUTPUT_CHAIN.md](OUTPUT_CHAIN.md) §4 established that some behaviours fire
without speaking, and that a skin should show a lamp instead of a voice. This
chapter is what happens *behind* the lamp: the silent thinking channel — a
real turn, fully thought, fully swallowed — which is the difference between a
companion who has an inner life and one who has an idle animation.

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
   pending topic ([INTEGRATION.md](INTEGRATION.md) §5's proactive path) and
   let the ordinary gates decide when.

## 4. Cost, honestly

An introspection is a full model call. That is the price of an inner life
that's real rather than performed, and the source's gates (solitude, one per
window, minimum gap) exist precisely to keep the spend rare — a handful of
turns a day, all during idle time that would otherwise be dead. Budget
version: keep the lamp wired to the *scheduler fire* and make the model call
conditional — the rhythm stays visible even when a given fire is skipped;
just accept that a skipped fire moves nothing (§2's honesty note applies).

---

*See also:* [OUTPUT_CHAIN.md](OUTPUT_CHAIN.md) §4 — the silent-behaviour
dispatch path this chapter fills in · [PROMPT_STACK.md](PROMPT_STACK.md) —
the partition the introspection turn reuses verbatim ·
[INTEGRATION.md](INTEGRATION.md) §4 — the heartbeat that carries the
scheduler these fires ride on.
