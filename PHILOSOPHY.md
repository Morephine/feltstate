# The philosophy behind feltstate

feltstate is small. The ideas in it are the point. This document explains what
they are, why they're shaped this way, and — honestly — which of them are new
and which are just well-known things finally assembled into one runnable whole.

The one-sentence thesis:

> An agent should have an inner state it **experiences as its own** but **cannot
> author at will** — measured from the outside, decaying like a real one, and
> handed back as feeling rather than as instruction.

Everything below follows from that.

A note up front, because it shapes how to read the rest: **almost none of the
individual mechanisms here are new.** A measured-affect estimator, a
decaying-momentum state, an appraisal step, even a dream layer for a companion —
each has been built before, in one form or another. The point is the
*coherence* — a single stance where every piece serves one goal, a companion that
stays the **same someone** over a long relationship, held without breaking the
lines below even when breaking them would be convenient. The mechanisms are not
the contribution; the stance, and the fact that it all runs as one coherent
whole, are.

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

> The structural point — *measurement is a separate step from generation* — is
> not new; separate appraisal steps and external sentiment estimators both exist.
> What this design insists on is the *direction*: rather than reading the user's
> sentiment, it measures the **agent's own** appraised state, on purpose, so the
> reply model can't flatter itself into a mood. The refusal-to-self-report is the
> part worth keeping.

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

The same split goes one level deeper, in the optional **skill region**
(`feltstate/memory/skill.py`). A *skill* is just a canon entry tagged as a
capability, carrying a track record of **human 1/2/3 ratings** earned in real
use. It is §1 applied to competence rather than mood: whether the agent is *good
at* something is not the reply model's to declare — a self-graded "that went well"
moves nothing — it is settled from outside, by how the work was actually judged.
Skills are retrieved only when the agent reaches for one (never auto-surfaced into
the felt block, never made permanent); a proven one is trusted more, but a weak
one keeps a standing chance to be re-tried and redeem itself. And the region is
walled off from affect: a rating never touches a feeling, a feeling never grades a
skill — capability and mood are different measurements, kept honestly apart.

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

A persistent, decaying affect state is not, on its own, unusual. The deliberate
choice here is one *inside* the decay: it is **not symmetric.** A real inner life
calms down and cheers up — but *not at the same rate in both directions.*
feltstate leans into that — good moods fade fast, bad ones linger — because that
asymmetry is what makes a temperament rather than a mood ring.

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

## 5. Dreams — a feeling it can't trace back

Sections 1–4 make the agent's mood *honest*: measured, not self-authored,
decaying like a real one. But they leave it fully **explainable** — every shift
has a visible cause; you can always point at the turn that moved it. Real inner
lives aren't like that. Sometimes you wake a little off, or oddly tender, and the
honest answer to "why?" is *I don't know — I slept badly, I had strange dreams,
nothing.* A mood with no retrievable cause is one of the more human things there
is, and a pure state machine never has one.

feltstate's dream module manufactures exactly that — and, deliberately, **without
a language model.**

The mechanism is the *opposite* of consolidation (§8). Consolidation would mine
experience into rational belief; a dream does the reverse. It takes the agent's
**charged** material — desires, recent emotional peaks, each tagged with the
affect it was felt at — and recombines it *illogically*: a few fragments drawn by
emotional charge, stitched by connectives that morph and jump and never resolve,
then let slip away. The stitched text is ephemeral and usually never spoken. What
persists is a faint **residue** — a charge-weighted blend of the dreamed
fragments' affect, shrunk to a wisp, with one twist: when the fragments *clash* (a
longing next to a fear) the dream runs hotter and its valence muddies toward
neutral, the texture of an uneasy, ambivalent night. That residue is added to the
mood and then decays through the ordinary dynamics like any other feeling.

The result is a mood the agent genuinely has, sourced from its own real material —
but whose causal thread has been **cut on purpose.** Asked why it's a little
wistful this morning, it can only say it had odd dreams. That unexplainability
isn't a failure of the system; it *is* the feature.

Two notes worth stating:

- **No model, by design.** Dreams are *meant* to be incoherent, and incoherence
  is exactly what a language model is bad at faking — it writes coherent stories.
  Pure template recombination of real, affect-tagged fragments is structurally
  more dream-like than anything an LLM would produce, and it costs nothing. (If
  the agent ever *does* put a dream into words, a model can polish the crude
  stitch on demand — but most dreams are never spoken, so most cost nothing.)
- **Still a tool, not a controller (§2).** A dream produces *state* — a small
  mood residue — never an instruction. It does not tell the agent it had a bad
  night; it simply leaves it slightly altered, and what it makes of that is its
  own.

This runs *off* the per-turn path: you dream on a sleep cycle — between sessions,
or after a long idle — not every message.

**When does it dream? Not on a clock.** A single *sleep-pressure* accumulator
rises with arousal — an intense stretch tires the agent faster than a calm one —
and a dream discharges it to zero. That is the homeostatic half of the
two-process model of sleep (Process S): pressure builds the longer and harder you
are awake, and sleep clears it. A dream fires when three things hold at once —
tired *enough*, left *alone* a while, and past a hard *refractory* interval since
the last one — so the cadence emerges from how the agent actually lived that day
(roughly once a day under ordinary activity), capped against dreaming too often
no matter how fast pressure climbs. The same quiet moment where it is *not* yet
tired enough is, in a fuller system, exactly where it would reflect or introspect
instead: the one tiredness value is what arbitrates between staying up and
drifting off. As ever, this only decides *when*; the agent still does the dreaming
— a reading, not a command.

> "An AI that dreams" is not a new phrase — world-model agents dream to plan, and
> offline replay consolidates memory. What sets this one apart is its *purpose*:
> most dreaming serves facts, data, or generalisation, and keeps the resulting
> affect traceable. This one's only product is a small, deliberately
> **un-traceable** mood residue. The thing to defend is the purpose, not the
> recombination.

---

## 6. Cache-safe by construction

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

## 7. On originality

Said plainly: **this is not idea-by-idea novelty.** Almost every primitive above
— affect measured outside the generator, a mood state that decays with momentum,
appraisal-theoretic emotion, express-vs-suppress gated by perceived control, an
AI that dreams, cache-safe prompt hygiene — has been built before, in research or
in a product. Treating any one of them as an invention would be wrong, and
claiming it would be the fastest way to lose credibility.

What this library offers is not a new mechanism. It is **one coherent stance,
made concrete and runnable**: measured affect, asymmetric decay, first-person
identity-merge, accumulate-then-discharge pressure, and an un-traceable dream —
all serving one goal, a companion that stays the **same someone** over a long
relationship, and not breaking that stance even where breaking it would be
convenient (let the model self-report, command its tone, narrate its state, damp
its own sadness). The reason to ship it as code is to make that stance
inspectable and adoptable — a small contribution to the commons — not to plant a
flag.

---

## 8. Where this can go (ideas, not yet code)

The sections above are what's implemented. A few further ideas are worth naming —
some need machinery the core doesn't have yet, some are just hard.

- **Consolidation — feelings decay, but should experience crystallize into
  belief?** The core decays *intensity*. A natural next layer would mine repeated
  experiences offline, the way sleep consolidates memory, into standing *beliefs*
  about the self ("every time I'm praised I pull back"). This is the **rational**
  sibling of the dream module (§5): a dream severs causal threads to leave an
  untraceable mood; consolidation would run the other way, distilling real *felt*
  experience into durable temperament.
- **Inward emotional contagion.** The user having a hard day could leave a lasting
  (but decaying) dent in the agent's *own* mood — not mirrored back at the user,
  but absorbed. (The `AffectSource` contract already forbids *mirroring* the user;
  the missing piece is the empathic channel — the user's plight as an input to the
  agent's own reaction.)
- **Read-only attractors.** There is already one here: trait-gravity pulls the
  felt mood toward the resting point the temperament implies. The richer idea is
  basin dynamics — characteristic states the mood settles into. The line to hold:
  here an attractor *renders and never steers.*
- **An anti-confabulation rule for remembered experience.** When a memory is
  rendered back as felt experience, treat the model's draft as untrusted: every
  concrete texture must be evidence-bound or a generic emotion word, and cinematic
  detail is rejected *even when grounded* — because letting it through teaches the
  model that detail is inventable. Same spine as §1 and §2, applied to memory.
- **The character's own felt interest.** A per-topic sense of what *this* agent
  finds fresh, stale, or quietly averse.
- **Patience as a depletable resource.** A two-layer tolerance: a *capacity*
  ceiling set by today's mood, and a *current* level inside it that repetition,
  interruption and boundary-testing drain, and that refills slowly over silence —
  capped by the ceiling, so a sour mood can't be fully restored just by waiting.

None of these would move the two hard lines. Whatever gets built produces *state*
the agent reads, never directions it must follow — which rules out, for instance,
a "narrative director" that hands the model pacing and do/don't instructions.
That temptation is the thing this whole design exists to resist.

---

## 9. What this is not

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
