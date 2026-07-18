# The style spectrum — delivery notes as an optional layer

The felt block tells the model *what the companion feels*. Most models mirror
that content faithfully — and deliver it in the same cadence they deliver
everything else. "I'm thrilled," typed at the exact tempo of a quarterly
report. The missing piece is **delivery**: how the mouth opens, before a word
of content changes.

This page describes a *style renderer*: a pure function from the affect state
to a few concrete, form-only **delivery notes** that ride under the felt
block. Reference implementation and every quoted output:
[`examples/style_spectrum.py`](../examples/style_spectrum.py).

---

## 0. The tension, addressed first

feltstate's core contract is *description, never instruction* — the engine
will not say "respond sadly" (README, design choice #6). A style layer is,
unavoidably, instruction. The resolution:

* it is **not part of the engine** — an app-side, opt-in renderer;
* it instructs **form only** — punctuation, sentence length, pace, doubled
  words, filler — never feeling ("be happy") and never content;
* the *what-I-feel* stays the felt block's job; the style layer only answers
  *how that feeling holds a pen*.

Whether you want it depends on your backend: strong models often deliver
correctly from the description alone; smaller ones benefit from the spec.
Trade-off in one line: **delivery notes buy consistency at a small cost in
spontaneity** — keep them coarse and sparse, and the cost stays small.

## 1. The four rules

1. **Form, never content.** A note may govern punctuation temperature,
   sentence length, doubled words, filler-word budget, opening softness. It
   may never name an emotion to perform or a thing to say.
2. **Off-neutral only.** A neutral state emits *no notes at all* — same
   silence-by-default economy as every other line in the stack
   ([PROMPT_STACK.md](PROMPT_STACK.md) §2, principle 3).
3. **Examples inline.** Models follow `("so so good")` better than the
   adjective "enthusiastic". Every directive that can carry a three-word
   example should.
4. **Hard cap.** At most three notes. Five directives read as a style sheet;
   two read as a mood.

## 2. The spectrum — real output

From the reference renderer (verbatim):

```text
1. bright and quick — high valence, high arousal, joy lit
delivery:
  - quick, light sentences; an exclamation is fine ("that landed!")
  - doubled words read as sparkle, use sparingly

2. pressed — a suppressed release owns the whole delivery
delivery:
  - keep sentences short; end lines a beat early, like words are being rationed
  - no exclamation marks; strip filler words entirely

3. low and slow — negative valence, low arousal
delivery:
  - slow it down: fewer clauses per sentence, soft closers, no rush to fill

4. anxious edge — negative valence, high arousal
delivery:
  - shorter breath: commas crowd in, sentences cut off earlier than usual

5. neutral — no notes at all
  (no delivery notes — neutral state, the default is silence)

6. reunion — returning after a felt gap
delivery:
  - open gently; acknowledge the gap once, lightly — then move on
```

The mapping logic worth copying:

| state signal | delivery shape |
|---|---|
| suppressed release (`*_suppress`) | **owns the whole delivery** — rationed words, early line-ends, zero exclamation; a held-in feeling flattens everything else |
| open release, by flavour | joy-burst → short bursts + doubled words; tears/collapse → trailing sentences, commas over full stops; anger → clipped, hard stops, no softeners |
| valence ≥ 0.4 ∧ arousal ≥ 0.6 | quick light sentences, exclamation allowed; joy bar ≥ 0.5 adds the sparkle note |
| valence ≤ −0.25 ∧ arousal ≤ 0.35 | slow: fewer clauses, soft closers, no rush to fill silence |
| valence ≤ −0.2 ∧ arousal ≥ 0.6 | short breath: crowding commas, sentences cut early |
| returning after a felt gap | open gently, acknowledge the distance once — then move on |

Note the *priority structure*: the suppressed release returns immediately —
when a feeling is being held in, no other note may contradict the holding.
Priority is part of the spec: later notes must never fight earlier ones.

## 3. Where the notes ride

Directly under the felt block, on the newest user message — same cache logic
as everything dynamic ([INTEGRATION.md](INTEGRATION.md) §2):

```text
[how I feel right now]
mood: sad | a little low, low energy
inside: pressure building, weighing a little | spilling over
right now: a thickness in my throat I'm swallowing back

delivery:
- keep sentences short; end lines a beat early, like words are being rationed
- no exclamation marks; strip filler words entirely

...today went badly. the launch slipped again.
```

Description above, delivery spec below, user's words last. The model reads a
feeling, then how that feeling writes, then what to answer.

## 4. Tuning it to your backend

* **Start with zero notes** and add bands only where you observe flat
  delivery — the spectrum is a patch kit, not a default-on subsystem.
* **Per-model dialect.** Some models over-obey ("no filler" produces
  telegrams); soften wording per backend rather than adding counter-notes.
* **Watch for spillover.** If notes start changing *content* (a "short
  sentences" note making answers less complete), the note is too strong —
  reword toward form ("end lines a beat early") or drop it.
* **Keep the bands aligned** with the felt block's own thresholds
  ([PROMPT_SHAPES.md](PROMPT_SHAPES.md)) so delivery never contradicts
  description — the block says "flat, drained" while the notes ask for
  exclamations is the failure mode to test for.
