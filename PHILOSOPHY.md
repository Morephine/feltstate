# The philosophy behind feltstate

feltstate is small. The design choices are the point. This document explains
what they are, why they are shaped this way, which established ideas they build
on, and what is distinctive about the architecture that connects them.

The one-sentence thesis:

> An agent should have an inner state presented to it in first-person form but
> **not authored by it at will** — independently appraised from the outside,
> using human-inspired asymmetric dynamics, and handed back as feeling rather
> than as instruction.
>
> (This is a prompt/interface design stance — not a claim about consciousness or
> subjective experience.)

Everything below follows from that.

feltstate builds on established ideas from affective computing, appraisal,
agent memory, selective forgetting, and prompt engineering. Its distinctive
contribution is the architecture formed by their combination: appraisal is kept
separate from generation, persistent state cannot be authored at will by the
reply model, memory has an inspectable lifecycle, and state influences
generation as context rather than command. The sections below explain why those
boundaries are held even when relaxing them would be convenient.

---

## 1. Externally estimated affect, not self-report

If you ask a language model "how do you feel?", it will tell you something
fluent and plausible. It is not lying, exactly — it just has no stable state to
consult, so it generates an answer that fits the conversation. Ask again after a
nudge and it will happily feel differently. A reply model can therefore generate affective language without consulting any
persistent state.

feltstate's first commitment is to take that decision **away from the reply
model**. A separate component — an `AffectSource` — *estimates* affect each turn
and writes it into a state the reply model does not control. The reply model
later *reads* that state, but it never gets to set it.

Why this matters:

- **The reply prompt cannot directly set the state.** State changes come from
  the configured source and integration rules rather than from a mood claim in
  the generated reply.
- **It's inspectable and testable.** Affect is numbers in a file. You can assert
  on them, plot them, replay them. "The agent is getting more guarded over this
  session" is an inspectable state change, not just prose.
- **It supports an outside view.** The same input lands differently on a wary
  agent than a trusting one, deterministically, because the reaction is grounded
  in state the source reads — not in a fresh reply-generation guess.

The `AffectSource` is deliberately an interface. The reference `KeywordSource`
is crude on purpose; a model-backed source can provide a richer estimate, but
it is still an estimate. The structural point is that **appraisal is a different
step from generation.**

> Separate appraisal steps and external estimators have clear precedents. The
> distinctive boundary here is their direction and ownership: the configured
> source estimates the character's appraised state, while the reply model cannot
> promote its own generated affective language into persisted state.

### Illustrative example

Two optional experimental adapters are referenced by the library:
[`kaishuiji/vheart-affect-v8`](https://huggingface.co/kaishuiji/vheart-affect-v8)
on a 1.5B base, and
[`kaishuiji/vheart-affect-v9`](https://huggingface.co/kaishuiji/vheart-affect-v9)
on a 4B base. They are loaded by `feltstate.sources.vheart.VheartSource`.
Training data is not released, and this repository provides no public
reproducible benchmark for them. Treat them as interface demos — closer to
research toys than production classifiers — not as model recommendations.

A few implementation notes from this run, not empirical claims:

- This run expanded the label vocab in stages rather than starting at
  the target size, and used anchor examples between stages.
- This run split the training centre of each label by context instead
  of collapsing all instances of a label to one point.
- This run emitted a primary/secondary blend with weights alongside the
  flat label.

These are choices, not prescriptions. A different fine-tune will make
different ones.

---

## 2. Tool, not controller

The tempting way to use affect is to write it into the prompt as instruction:
"You are feeling sad and guarded; respond accordingly." Don't. This one is
learned the hard way: **injecting behavioural rules makes the model worse** —
flatter, more performative, more obviously following a script. A whole subsystem
that auto-injected "remember to mention X" / "don't repeat yourself" rules can be
built, and then has to be ripped out for exactly this reason.

So feltstate draws a hard line: **the library produces state; it never produces
commands.** It gives the reply model a descriptive state summary and stops
there. The model may use that context when generating a response, but the
library does not prescribe a tone or action.

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
the reply model mid-sentence. That mirrors how affect is estimated by a separate
source rather than self-reported (§1): in both cases appraisal is a different
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
skill — capability and mood are different signals, kept deliberately apart.

### Memory has a lifecycle, not just a retrieval score

`Canon` stores compact 5W1H facts with salience, repetition, recall effects,
correction, retraction, and bi-temporal history. The optional
`feltstate.memory.lifecycle` package goes further: a memory can be born with a
source-and-affect fingerprint, retain lineage when facts are fused, age according
to its declared kind, become eligible for a pure death plan, and be removed by a
tombstone-first reaper that can also clean explicitly supplied snapshots. A
hash-linked ledger makes unexplained mutation or disappearance detectable when
the ledger is treated as the trusted record.

The lifecycle now also has an explicit distillation and traceback path. A
caller-produced summary can pass through a zero-LLM consistency gate before it
is sealed; genealogy can then be drilled through `src` and fusion lineage back
to raw source pointers, and those pointers can be resolved across their complete
source time range by a caller-owned loader. When the application can reproduce
the exact archived source text, the same report can verify its sealed hash. A
partly missing genealogy does not erase the
raw pointers copied into a surviving fused memory.

That still does not make provenance automatic. `Canon`, fingerprints, and the
conversation archive are deliberately separate stores. The application must
keep a resolver for fingerprints and a loader for source transcripts; if the
referenced source material is gone, a hash can verify no text and recover no
text. The consistency gate is lexical evidence checking, not a semantic theorem
prover, and its default language tables are English-oriented.

This is provenance and tamper evidence, **not encryption**. SHA-256 does not hide
content, and an attacker able to rewrite all files can also recompute ordinary
hashes. The value is an inspectable lifecycle: where a memory came from, how it
changed, why it was retired, and whether its disappearance was authorised.

---

## 3. First-person context

There are two common ways to provide state to a reply model. One is
third-person data:

```
[affect] valence=-0.3 arousal=0.6 labels=[anxious] trust=0.42
```

A reply model may narrate that data directly: "I see that my trust level is
0.42." That can be undesirable in a companion interface.

feltstate instead renders a compact first-person block in plain language:

```
[how I feel right now]
now Sun morning 9:56
with you: warming · mostly trusting · mostly safe
mood: relieved | level, low energy
inside: pressure clear | settled
underneath: spirits steady · nerves even · moderately curious · even-keeled
```

Paired with a framing instruction that the block is context rather than text to
quote, this reduces direct narration of numeric state. It remains a prompt and
interface technique: the block influences generation but does not establish
subjective experience.

This is why the render layer translates every value into a discrete human phrase
("close", "mostly safe", "joy bright") instead of a number. It reads more like a compact state summary than a dashboard.

---

## 4. Emotion decays — and not symmetrically

A persistent, decaying affect state is not, on its own, unusual. The deliberate
choice here is one *inside* the decay: it is **not symmetric.** The state
relaxes at different rates in different directions.
feltstate leans into that — good moods fade fast, bad ones linger — because that
asymmetry is what makes a temperament rather than a mood ring.

feltstate models three timescales of decay:

> **Tick-scaling note.** All decay is scaled by wall-clock elapsed time using
> `max(1.0, elapsed_seconds / 60)`. This means: intervals longer than one
> minute receive approximate time-proportional scaling; every call applies at
> least one unit of decay regardless of elapsed time. Calling `tick()` ten times
> in a minute therefore applies ~10 units of decay rather than 1 — the floor
> applies per call, not per minute. This is not strict frequency invariance; it
> is approximate time-scaling for spaced calls with a per-call minimum.

**Traits (slow, asymmetric).** Long-term temperament — depression, optimism,
anxiety, curiosity — moves by an EWMA. The trick is that all traits *rise* at
the same rate but *relax back to neutral* asymmetrically: optimism and curiosity
fade several times faster than depression and anxiety linger. That single
asymmetry is inspired by two human patterns — *hedonic adaptation* (you stop
noticing good things) and *rumination* (bad things stick) — though no controlled
experiment or benchmark validates this mapping.
A good afternoon doesn't make a gloomy temperament sunny for a week; a betrayal
colours things long after.

**Mood (fast).** Felt valence/arousal track recent readings quickly, but are
*pulled* toward the resting point the traits imply. A depression-leaning state
can still move upward — and still never gets as bright as an agent without
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

**Plasticity — what fires, sensitizes.** Decay says feelings fade; plasticity
says the *capacity* to feel them doesn't stay fixed. Each pressure bar carries
a sensitivity (0.5 = neutral): every tick whose raw inflow clears a charge
threshold nudges it up by a **micro** amount — one message is ~1e-5, so no
single conversation bends a character — and inflow is multiplied by
``1 + k × (sensitivity − 0.5)``, so the dimensions life keeps hitting become
genuinely easier to stir. The counterweight is healing: sensitivity relaxes
toward 0.5 by a fraction of a percent per day, paced by
``relationship.safety`` — a safe bond softens carved edges faster (half-life
roughly a year at zero safety, ~86 days at full). Hits are metered on the raw
stimulus (never the amplified echo, which would feed back), releases don't
meter (venting is not new experience), and the loop is zero-LLM. The intended
scale is ~180 days: live half a year of cheerful days and the joy dimension
is measurably — barely — quicker to light than the day the state was born.
Runnable: [`examples/plasticity.py`](examples/plasticity.py), the same 180
days lived under two safeties.

The vocabulary throughout comes from appraisal theory and affective-computing
literature; the numbers do not. Every rate, threshold and asymmetry here is a
**character parameter, not a psychological measurement** — chosen and adjusted
the way animators tune a movement curve: iterated against long-running use
until the temperament feels coherent, then exposed as a dial. `config.py`
holds every one with a one-line rationale, per-character values ride
`PersonaDials`, and the defaults amount to one hand-tuned reference
personality — not a claim about people in general. Retuning them is not
deviating from the model; it is the intended way to write a *different*
character: quicker to forgive, slower to warm, harder to tire. The honest
test for these numbers is whether the resulting character stays coherent over
months, not whether a constant agrees with a published one — they are not
fitted to human data, and their job does not require them to be.

---

## 5. Dreams — a state shift with no surfaced cause

Sections 1–4 leave each state change linked to an explicit input. The dream
module adds an optional state transition whose detailed source fragments are not
placed in the reply-model context. This can produce a small unexplained shift in
later generation without claiming to simulate dreaming or human experience.

feltstate's dream module manufactures exactly that — and, deliberately, **without
a language model.**

The mechanism is the *opposite* of consolidation (§8). Consolidation would mine
experience into rational belief; a dream does the reverse. It takes stored **charged** fragments — for example desires or recent events,
each tagged with an affect estimate — and recombines it *illogically*: a few fragments drawn by
emotional charge, stitched by connectives that morph and jump and never resolve,
then let slip away. The stitched text is ephemeral and usually never spoken. What
persists is a faint **residue** — a charge-weighted blend of the dreamed
fragments' affect, shrunk to a wisp, with one twist: when the fragments *clash* (a
longing next to a fear) the dream runs hotter and its valence muddies toward
neutral, the texture of an uneasy, ambivalent night. That residue is added to the
mood and then decays through the ordinary dynamics like any other feeling.

The result is a persisted mood residue derived from stored, affect-tagged
fragments, while its explicit causal thread is **omitted on purpose.** Asked why it's a little
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
drifting off. As ever, this only decides *when*; the dream mechanism still generates the
event — a state transition, not a command to the reply model.

> “Dreaming” is used in AI for several different mechanisms, including planning
> and offline replay. Here it names a deliberately narrower feature: zero-LLM
> recombination whose only persistent product is a small mood residue, while the
> source fragments are **not surfaced to the reply model as an explicit cause**.

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

This follows established prompt-cache hygiene: keep the static prefix stable
and append dynamic material rather than rewriting the top of the prompt. Its
importance here is architectural rather than ornamental: a persistent companion
needs a state-injection path that does not turn every small state change into a
full-prefix cache miss.

---

## 7. What is distinctive

feltstate does not claim exclusive ownership of the fields it builds on. Affect
appraisal, persistent agent memory, asymmetric state dynamics, proactive
scheduling, selective forgetting, and prompt-cache hygiene all have established
precedents.

Its contribution is the **specific architecture and the boundaries between its
parts**, made concrete and runnable:

- appraisal is separate from reply generation;
- the reply model cannot freely author persisted affect or self-grade skills;
- structured memories can fade, strengthen, change, retain provenance, merge,
  and pass through an auditable deletion lifecycle;
- affect, relationship, pressure, and memory evolve across different
  timescales;
- persistent state is presented as first-person context rather than instruction;
- proactive behaviour is gated through presence, idle time, cooldowns, quotas,
  and successful delivery;
- dynamic context is injected without continually rewriting the static prompt.

Shipping these choices as running code — not a paper, not a description — makes
them inspectable, testable, replaceable, and open for others to adopt or
challenge. The synthesis is the claim: an appraisal-ownership boundary, a
provenance-tracked memory lifecycle that can end in an audited death, and an
off-path dream whose only product is mood — held together under one stance.

---

## 8. Where this can go (ideas, not yet code)

The sections above are what's implemented. A few further ideas are worth naming —
some need machinery the core doesn't have yet, some are just hard.

- **Consolidation — feelings decay, but should experience crystallize into
  belief?** The core decays *intensity*. A natural next layer would mine repeated
  experiences offline, the way sleep consolidates memory, into standing *beliefs*
  about the self ("every time I'm praised I pull back"). This is the **rational**
  sibling of the dream module (§5): a dream severs causal threads to leave a
  mood not surfaced as an explicit cause; consolidation would run the other way,
  distilling stored, affect-tagged
  experience into durable state.
- **Inward emotional contagion.** The user having a hard day could leave a lasting
  (but decaying) dent in the character's stored mood — not mirrored back at the user,
  but absorbed. (The `AffectSource` contract already forbids *mirroring* the user;
  the missing piece is the empathic channel — the user's plight as an input to the
  character's estimated reaction.)
- **Read-only attractors.** There is already one here: trait-gravity pulls the
  felt mood toward the resting point the temperament implies. The richer idea is
  basin dynamics — characteristic states the mood settles into. The line to hold:
  here an attractor *renders and never steers.*
- **An anti-confabulation rule for remembered experience.** When a memory is
  rendered as first-person context, treat the model's draft as untrusted: every
  concrete texture must be evidence-bound or a generic emotion word, and cinematic
  detail is rejected *even when grounded*. Same spine as §1 and §2, applied to
  memory.
- **Per-topic interest state.** A stored estimate of which topics the character
  currently treats as fresh, stale, or aversive.
- **Patience as a depletable resource.** A two-layer tolerance: a *capacity*
  ceiling set by today's mood, and a *current* level inside it that repetition,
  interruption and boundary-testing drain, and that refills slowly over silence —
  capped by the ceiling, so a sour mood can't be fully restored just by waiting.

None of these would move the two hard lines. Whatever gets built produces *state*
for the reply model, never directions it must follow — which rules out, for
instance, a "narrative director" that hands the model pacing and do/don't
instructions.

---

## 9. What this is not

- **Not a claim about consciousness.** feltstate models persistent affective
  state so behaviour can be more consistent over time. It says nothing about
  subjective experience or genuine feeling.
- **Not AGI, not a personality.** It's a state engine. The personality, the
  values, the voice are yours to bring.
- **Not a substitute for good prompting or a good base model.** It's a layer
  that makes a capable model *continuous* — it won't rescue a weak one.

The goal is narrow: across a long relationship, the system should produce a
more consistent character — one whose stored state changes, persists, recovers,
and influences later replies without becoming an instruction script.
