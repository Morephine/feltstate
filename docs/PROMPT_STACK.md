# The prompt stack — partition, sandwich, and the forget probe

Three decisions shape every companion turn's prompt: **what is static** (and
therefore cacheable), **what is per-turn** (and where in the array it rides),
and **how the persona survives long stretches of work without being re-pasted
every turn**. This page is the reasoning behind all three; the concrete message
arrays are in [PROMPT_SHAPES.md](PROMPT_SHAPES.md), and the assembly code path
is `companion/round.py`.

---

## 1. The partition — every line earns its side

| static system prompt (byte-stable, cached) | per-turn injection (rides the newest user message) | why it sits there |
|---|---|---|
| who the companion *is* — persona, voice, values | — | changes never; every byte here is cache prefix |
| ground rules ("the felt block is state, not a command") | — | a rule repeated per-turn would burn tokens to say the same thing |
| tool schemas (memory tools, etc.) | — | schemas are large and constant — the classic cache win |
| — | `[how I feel right now]` block | changes every turn *by design* — in the prefix it would invalidate the whole cache each time |
| — | the time line ("3 days since we last spoke · now Sat 12:25") | pure function of the clock; the most volatile line in the stack |
| — | conditional lines (release texture, aftertaste, tone) | exist only when owed; absence keeps the common case short |

Two amplifiers make the dynamic side cheap:

* **Coarse bands.** A value drifting 0.51 → 0.53 renders the same phrase
  (see the variant table). Only felt-sized moves change bytes.
* **Silence as a variant.** Joy under 0.20, tension under 0.10, neutral tone —
  the line is omitted. The calm case is the short case.

## 2. The sandwich — and the physics of the bottom slice

```text
┌──────────────────────────────────────────┐
│ system: persona + rules + tools          │  top bread — static, cached
├──────────────────────────────────────────┤
│ prior turns (append-only history)        │  filling — stable once written
├──────────────────────────────────────────┤
│ newest user message, rebuilt:            │  bottom bread — baked fresh
│   [felt block]                           │    each turn
│   [time line]                            │
│   [conditional lines]                    │
│   <the user's actual text>               │
└──────────────────────────────────────────┘
```

The bottom slice is the only part the model reads with **maximum recency**, so
its internal order matters. Three ordering principles, applicable to any
extra content you ride there (recalled memories, event notices, inner-voice
lines):

1. **Binding closest to the user's line.** Whatever must most shape *this*
   reply sits nearest the actual user text. If you inject recalled memories or
   surfaced events with varying importance, sort ascending so the strongest
   lands last — right above the user's words.
2. **Ephemeral colour dead-last or first — never mid-pile.** A one-turn-only
   flavour (a mood flash, an inner-voice line) either leads the block as
   scene-setting or closes it as the freshest note; buried in the middle it
   reads as stale context.
3. **Conditional lines appear only when owed.** A reminder, a warning, a
   special state — if the condition isn't met, the line does not exist. This
   is both a token economy and a salience economy: a line that always appears
   is a line the model learns to skim.

Principle 3 has a powerful special case, worth its own section:

## 3. The forget probe — persona upkeep that costs nothing until it's needed

**The problem.** Over a long agentic stretch (many tool calls, long outputs),
character drifts: the companion slides into assistant-ese and stops sounding
like itself. The blunt fixes are both bad: re-pasting an identity reminder
*every* turn burns tokens forever and dulls into wallpaper; never reminding
lets drift compound unnoticed.

**The pattern.** Require the model to perform one tiny, checkable act in every
reply — a *probe operation*. The natural choice here is the sentence-initial
`[emotion]` tag, because the output chain already wants it (it colours the
voice and can drive the face — see [OUTPUT_CHAIN.md](OUTPUT_CHAIN.md)). Then:

* each turn, check the *previous* reply for the probe —
  `extract_emotion_tag()` in `companion/round.py` is exactly this check;
* **probe present** → the model is still inhabiting the character → append
  nothing. Zero tokens, zero noise.
* **probe missing** → it forgot itself → append one identity line to the next
  turn's dynamic block ("you are <persona>; feel through the state block, tag
  your sentences") → the reply re-anchors → the line disappears again.

```python
# app-side wiring sketch (the probe primitive ships in companion/round.py)
drifted = extract_emotion_tag(last_reply) is None
extra = [IDENTITY_REMINDER] if drifted else []
messages = assemble(system, history, felt_block, *extra, user_text)
```

**Why it works.** The reminder is *event-triggered self-repair*: it costs
tokens only in the turn after a drift, appears rarely enough to stay salient
when it does, and the loop is self-limiting — one drift, one reminder, one
recovery. The probe doubles as telemetry: log the misses and you have a drift
rate for free.

**Choosing a probe.** Any per-turn act works if it is (a) cheap for the model,
(b) trivially machine-checkable, and (c) something a *drifted* model naturally
stops doing. The emotion tag scores on all three — an assistant-mode reply is
precisely the kind that forgets to feel.

---

*See also:* [PROMPT_SHAPES.md](PROMPT_SHAPES.md) for the rendered blocks these
rules produce, and [INTEGRATION.md](INTEGRATION.md) §2 for the caching
economics in the context of the full loop.
