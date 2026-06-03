# The philosophy behind feltstate

feltstate is small. The ideas in it are the point. This document explains what
they are, why they're shaped this way, and — honestly — which of them are new
and which are just well-known things finally assembled into one runnable whole.

The one-sentence thesis:

> An agent should have an inner state it **experiences as its own** but **cannot
> author at will** — measured from the outside, decaying like a real one, and
> handed back as feeling rather than as instruction.

Everything below follows from that.

---

## 1. Ground truth, not self-report

If you ask a language model "how do you feel?", it will tell you something
fluent and plausible. It is not lying, exactly — it just has no stable state to
consult, so it generates an answer that fits the conversation. Ask again after a
nudge and it will happily feel differently. Companion products built on this
("I feel so happy you're back!") are performing affect, not having it.

feltstate's first commitment is to take that decision **away from the reply
model**. A separate component — an `AffectSource` — *measures* affect each turn
and writes it into a state the reply model does not control. The reply model
later *reads* that state, but it never gets to set it.

Why this matters:

- **It can't be flattered into a mood.** The state moves because of what was
  said and how the agent's standing temperament reacts to it — not because the
  prompt asked for a vibe.
- **It's inspectable and testable.** Affect is numbers in a file. You can assert
  on them, plot them, replay them. "The agent is getting more guarded over this
  session" is a measurement, not a vibe.
- **It supports an outside view.** The same input lands differently on a wary
  agent than a trusting one, deterministically, because the reaction is grounded
  in state the source reads — not in whatever the LLM feels like saying.

The `AffectSource` is deliberately an interface. The reference `KeywordSource`
is crude on purpose; the real signal comes from an `LLMSource` (a *separate*
model call whose only job is to measure) or a classifier you fine-tune. The
point is structural: **measurement is a different step from generation.**

> Prior art, honestly: "compute emotion outside the generator" is not new
> (Chain-of-Emotion, PAD-state agents, small-empathy-model plugins). What's
> uncommon is doing it for a *conversational companion* with a *dedicated* model
> and an explicit refusal to let the generator self-report.

---

## 2. Tool, not controller

The tempting way to use affect is to write it into the prompt as instruction:
"You are feeling sad and guarded; respond accordingly." Don't. This one is
learned the hard way: **injecting behavioural rules makes the model worse** —
flatter, more performative, more obviously following a script. A whole subsystem
that auto-injected "remember to mention X" / "don't repeat yourself" rules can be
built, and then has to be ripped out for exactly this reason.

So feltstate draws a hard line: **the library produces state; it never produces
commands.** It hands the agent a description of how it feels and stops there.
Whether to be terse because it's tired, or warm because trust is high, or to
change the subject because a boundary bar is full — that's the agent's call,
made the way it makes every other call, from context.

Concretely:

- The rendered block is descriptive ("pressure low, joy bright"), not
  imperative ("be cheerful").
- Memory (`Canon`) is a tool the agent *chooses* to read and write — the library
  never auto-injects memories into the prompt. Decay, dedup, and visibility are
  handled silently; *what to remember* is the agent's.
- Constraints that are genuinely necessary (don't repeat, stay safe) belong in
  the sampling layer or in tool design — not as scolding prompt text.

The shape of the rule: **manage capability, don't forbid behaviour.** Give the
agent true information about its own state and trust it to act like itself.

The same discipline shapes memory's write side. Facts worth keeping are proposed
by a *separate* extraction pass — an optional second model call — not decided by
the reply model mid-sentence. That mirrors how affect is measured by a separate
source rather than self-reported (§1): in both cases measuring is a different
step from generating, and the agent confirms what it actually keeps rather than
having memory written behind its back.

---

## 3. Identity-merge

There are two ways to hand an agent its state. One is third-person data:

```
[affect] valence=-0.3 arousal=0.6 labels=[anxious] trust=0.42
```

The agent reads that and, being helpful, tends to *narrate* it: "I see that my
trust level is 0.42." That's immersion-breaking and, worse, it teaches the agent
to treat its feelings as external readouts.

feltstate renders the other way — first person, in plain language, as the
agent's own felt sense:

```
[how I feel right now]
close · trusted · mostly safe · no friction
curious, content | calm, mild energy
```

Paired with a single framing instruction in your system prompt ("the block
below is your own inner state, not information someone gave you — never say
'my affect shows' or read the numbers out"), this is **identity-merge**: the
state stops being data *about* the agent and becomes the agent's own mood,
shaping *how* it speaks rather than something it reports.

This is why the render layer translates every value into a discrete human phrase
("close", "mostly safe", "joy bright") instead of a number. It reads like an
inner weather report, not a dashboard.

---

## 4. Emotion decays — and not symmetrically

This is the piece the ecosystem was actually missing. Memory layers decay the
*relevance of facts*; almost nothing decays the *intensity of feeling*. A real
inner life does the latter constantly: you calm down, you cheer up, the edge
comes off — and crucially, **not at the same rate in both directions.**

feltstate models three timescales of decay:

**Traits (slow, asymmetric).** Long-term temperament — depression, optimism,
anxiety, curiosity — moves by an EWMA. The trick is that all traits *rise* at
the same rate but *relax back to neutral* asymmetrically: optimism and curiosity
fade several times faster than depression and anxiety linger. That single
asymmetry reproduces two well-documented human patterns at once — *hedonic
adaptation* (you stop noticing good things) and *rumination* (bad things stick).
A good afternoon doesn't make a gloomy temperament sunny for a week; a betrayal
colours things long after.

**Mood (fast).** Felt valence/arousal track recent readings quickly, but are
*pulled* toward the resting point the traits imply. A depression-leaning agent
can be genuinely cheered — and still never gets as bright as an agent without
that weight. The ceiling is set by who it is.

**Pressure (threshold + release).** Emotion isn't one dial; it's five reservoirs
— sadness, anger, anxiety, boundary, joy — filling independently. Whichever
crosses threshold first is what gets *released* (a good cry, a flash of anger, a
withdrawal, a burst of delight), after which it **settles to a floor, not to
zero**, and leaves an aftertaste. Two design choices make this feel alive rather
than mechanical:

- *Valence-opposite inhibition* — sadness rising suppresses joy and vice versa.
  You don't belly-laugh mid-sob.
- *Express vs suppress is gated by power* — a Lazarus-style appraisal of
  perceived control (built from optimism, low depression/anxiety, safety,
  closeness). High power → the feeling is expressed; low power → it's held in.
  Same pressure, different surface, depending on whether the agent feels safe
  enough to show it.

**Imprints (optional, permanent, symmetric).** Some moments don't decay. A deep
wound or a deep kindness leaves a one-time permanent shift plus a faint echo
that can resurface when the right thing is mentioned again. The non-obvious
design rule here is *symmetry*: if only wounds were permanent, the agent would
drift colder forever. Warmth has to be able to leave a permanent mark too.

**Relationship (slow, and two-sided).** The bond with the user is a state as
well — closeness, trust and felt safety drift up over warm turns and down over
cold ones; tension rises on friction and eases on its own; and *repair history*
only ever accumulates. That last one is deliberate: having fought and come back
before is trust capital a single rough patch shouldn't erase, so it never
decays. The rates are all small and asymmetric — a bond is built over many
exchanges rather than declared in one, and trust is lost faster than it is built.

**The small textures.** A few cheaper signals keep the state from reading as
mechanical: a *tide* (is the mood climbing or sinking, read from its recent
trajectory), a *mixed feeling* (a second, opposing note under the primary one —
"relieved tinged with sad"), and an *aftertaste* that carries the previous
turn's flavour forward so feeling doesn't snap between turns.

And one thing that runs the other way. Not all feeling decays: **anticipation**
is the mirror image. A looked-forward-to event holds a *rising* joy floor that
climbs as the date nears and pays out when it arrives — the dopamine of expecting
a good thing, modelled as accumulation toward a point in time rather than
relaxation away from one.

The grounding throughout is appraisal-theory and basic-emotion psychology
(Lazarus, Bandura's self-efficacy, Plutchik, Tomkins/Izard) — not because the
agent *has* feelings, but because borrowing the *dynamics* of real ones is what
makes the behaviour read as coherent over time instead of moment-to-moment.

---

## 5. Cache-safe by construction

A companion that runs all day re-sends a large, mostly-static prompt every turn.
If you mutate the top of that prompt each turn — stamping in the current time, a
turn counter, or a freshly rendered state block — you invalidate the prompt
cache every single turn and pay full price forever.

feltstate is built so you don't have to:

- **Static stays static.** Your persona / system prompt is the cached prefix.
  feltstate never asks you to change it per turn.
- **Dynamic rides the newest message.** `build_injection()` puts the felt block
  on the *latest user message* — after the cached prefix — so the prefix stays
  byte-identical and keeps hitting cache.
- **Discrete phrasing keeps even the dynamic part stable.** Because the render
  translates values into discrete buckets ("close", not "0.79"), small tick-to-
  tick changes usually don't change the *text* at all — so adjacent turns often
  render identically.
- **Time sense respects the same discipline.** The "how long since we talked"
  line only appears after a real gap, and uses fuzzy buckets that change slowly;
  the precise clock reading only rides the re-engagement turn.

None of this is a new invention — "static on top, dynamic on bottom, append
don't prepend" is known prompt-cache hygiene. It's here because a *persistent
companion* lives or dies on it: shipping the state loop without cache-safe
injection would make running one prohibitively expensive.

---

## 6. What's new, what's not

Stated plainly, to be precise about novelty:

**Not new (don't claim it):**
- Computing emotion outside the generator. Done before.
- "Don't let the model self-report its feelings." A known critique.
- Fact memory with a time dimension. Zep/mem0 do it, more maturely.
- Cache-safe injection discipline. Standard engineering.

**New, or at least genuinely rare:**
- **Decaying *feeling* state** (not fact relevance), with asymmetric adaptation.
  This is the real gap — the surveyed systems don't have it.
- A **dedicated** affect measurer for a **conversational companion** that
  **refuses self-report** — the three together aren't something you'll find in
  public implementations.
- The **whole loop assembled**: measured ground-truth affect → decaying state →
  first-person identity-merge render → cache-safe injection → the agent decides,
  the library never commands. Each part has precedent; the assembled, runnable,
  philosophy-consistent whole did not exist off the shelf.

The honest framing isn't idea-by-idea novelty — several pieces above have prior
art if you squint. It's the *combination*. Survey the field and what no existing
system does is hold all three commitments at once: the literature repeatedly
breaks exactly the lines drawn here. Emotion gets used to *control* the agent's
behaviour; durable affect picked up from the user is treated as a *bug to
mitigate* rather than a designed feature; and "AI emotion" is almost always
self-reported. A companion-affect kernel that is ground-truth, render-only, and
identity-merged *at the same time* is the actual empty space.

The point of this project isn't a claim to have invented affective computing. It
is to provide that specific, opinionated whole as code you can run, inspect, and
adapt.

---

## 7. Where this can go (ideas, not yet code)

The sections above are what's implemented. A few further ideas are worth naming —
some need machinery the core doesn't have yet, some are just hard. A couple are
places the ecosystem has genuinely left empty; a couple only *look* novel, said
honestly below.

- **Consolidation — feelings decay, but should experience crystallize into
  belief?** The core decays *intensity*. A natural next layer would mine repeated
  experiences offline, the way sleep consolidates memory, into standing *beliefs*
  about the self ("every time I'm praised I pull back"). The word "dream" is
  taken in this space, but the incumbents do bizarre-narrative augmentation or
  factual dedup; consolidating real *felt* experience into temperament is an open
  slice.
- **Inward emotional contagion.** The user having a hard day could leave a lasting
  (but decaying) dent in the agent's *own* mood — not mirrored back at the user,
  but absorbed. This is the clearest gap of the lot: the field studies durable
  user-to-AI affect transfer as a *risk to mitigate* and ships only real-time
  mimicry. A bounded, ground-truth, *designed* version is unoccupied. (The
  `AffectSource` contract already forbids *mirroring* the user; the missing piece
  is the empathic channel — the user's plight as an input to the agent's own
  reaction.)
- **Read-only attractors.** There is already one here: trait-gravity pulls the
  felt mood toward the resting point the temperament implies. The richer idea is
  basin dynamics — characteristic states the mood settles into. The honest novelty
  isn't attractors (neuroscience and RL have them); it's that here they *render
  and never steer*, where the nearest prior art uses emotion as a control signal.
- **An anti-confabulation rule for remembered experience.** When a memory is
  rendered back as felt experience, treat the model's draft as untrusted: every
  concrete texture must be evidence-bound or a generic emotion word, and cinematic
  detail is rejected *even when grounded* — because letting it through teaches the
  model that detail is inventable. Same spine as §1 and §2, applied to memory.
- **The character's own felt interest.** A per-topic sense of what *this* agent
  finds fresh, stale, or quietly averse — distinct from a recommender, and from RL
  exploration bonuses, which is where the prior art lives.
- **Patience as a depletable resource.** A two-layer tolerance: a *capacity*
  ceiling set by today's mood, and a *current* level inside it that repetition,
  interruption and boundary-testing drain, and that refills slowly over silence —
  capped by the ceiling, so a sour mood can't be fully restored just by waiting.
  A "resource depletion" dynamic most affect models don't have.

None of these would move the two hard lines. Whatever gets built produces *state*
the agent reads, never directions it must follow — which rules out, for instance,
a "narrative director" that hands the model pacing and do/don't instructions.
That temptation is the thing this whole design exists to resist.

---

## 8. What this is not

- **Not a claim about consciousness.** feltstate models the *dynamics* of an
  inner life so behaviour reads as coherent. It says nothing about whether
  anything is felt. That debate is out of scope by design.
- **Not AGI, not a personality.** It's a state engine. The personality, the
  values, the voice are yours to bring.
- **Not a substitute for good prompting or a good base model.** It's a layer
  that makes a capable model *continuous* — it won't rescue a weak one.

The goal is narrow and honest: an agent that, across a long relationship, feels
like the same someone — who remembers, who can be hurt and can heal, whose good
moods fade and whose bad ones pass, and who is never just reading its own mood
off a screen.
