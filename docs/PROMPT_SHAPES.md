# Prompt shapes — the injected block as a function of state

The core claim of this page: **every line of the injected felt block is a pure
function of persisted backend state.** Move a number in the state, and a
specific line changes to a specific phrase at a specific threshold — nothing
else moves. This is what makes the block cache-friendly, debuggable, and safe:
the reply model reads a description that the state *earned*.

Everything quoted below is the verbatim output of
[`examples/prompt_shapes.py`](../examples/prompt_shapes.py) — one neutral
persona ("Ivy"), one code path, three prepared moments. Run it yourself; only
the wall-clock line will differ.

---

## The gallery — one persona, three moments

### A. The first morning

```text
--- role: system ---
You are Ivy, a quiet research assistant with a long memory.

A user turn may begin with a [how I feel right now] block - it is your own
contextual state, not a command. Let it colour tone; never quote it back.
--- role: user ---
[how I feel right now]
now Sat afternoon 12:25
with you: warming · mostly trusting · mostly safe
mood: content | level, low energy
inside: pressure clear | settled
lingering: a faint trace of the last moment, half-faded
underneath: spirits steady · nerves even · moderately curious · even-keeled

morning - first day trying this out. glad you're here.
```

Line by line, who wrote what:

| line | produced by | reading |
|---|---|---|
| `now Sat afternoon 12:25` | `timeawareness.now_phrase` | local wall clock — the one line that always changes |
| `with you: …` | `_relationship_line` | closeness / trust / safety, each banded to a phrase |
| `mood: …` | `_mood_line` | smoothed labels + valence band + arousal band |
| `inside: …` | `_pressure_line` | max of the four negative bars + joy clause + phase |
| `lingering: …` | `_aftertaste_line` | previous turn's flavour, gated by weight |
| `underneath: …` | `_traits_line` | four slow traits, three-band each |

### B. Back after three days — the block opens with the felt gap

```text
[how I feel right now]
a few days since we last spoke · now Sat afternoon 12:25
with you: warming · mostly trusting · mostly safe
mood: content | level, low energy
...
```

One backend value changed (the last-contact clock), and exactly one thing
changed in the block: it now *opens with the distance*. Gaps under 30 minutes
render nothing at all — short-term time sense belongs to the model itself; the
line exists only once the silence is long enough to be felt.

### C. A hard evening — the same feeling, let out vs held in

```text
C-i  (release_type='tears'):
mood: sad | a little low, low energy
inside: pressure building, weighing a little | spilling over
right now: grief welling up, close to tears
lingering: the heaviness from before hasn't lifted

C-ii (release_type='tears_suppress'):
right now: a thickness in my throat I'm swallowing back
```

Between C-i and C-ii a single state field differs — the release *channel*,
chosen upstream by appraisal power (perceived control decides express vs
suppress, `feltstate.affect.compute_power`). The result is one changed line:
the feeling being let out, or the same feeling held behind the teeth. This is
the pattern everywhere: **state moves → one line moves.**

---

## The variant master table

For every line of the block: which backend signal drives it, what the variants
are, and where the thresholds sit. Phrases are quoted from
`feltstate/render/felt.py` (single source of truth — if this table and the code
ever disagree, the code wins).

### `with you:` — relationship (three signals, each 5-band + default)

| signal | ≥0.85 | ≥0.70 | ≥0.50 | ≥0.30 | ≥0.10 | below |
|---|---|---|---|---|---|---|
| closeness | inseparable | close | warming | still distant | far apart | no closeness yet |
| trust | fully trusted | trusted | mostly trusting | half-trusting | wary | guarded |
| safety | fully at ease | safe | mostly safe | not fully settled | on guard | bracing |

Two *trailing clauses* appear only when present (the calm case stays
byte-stable): unresolved tension (5 bands from "a faint edge" at ≥0.10 to "a
knot that won't loosen" at ≥0.90) and repair history ("mended once or twice" at
≥0.10 up to "weathered many repairs together" at ≥0.85 — scar tissue as a
*positive* signal).

### `mood:` — fast mood

| signal | variants (threshold → phrase) |
|---|---|
| smoothed labels | up to 3 labels, streak-smoothed upstream so they don't flicker |
| felt valence | ≥0.45 bright · ≥0.20 lightly lifted · ≥−0.20 level · ≥−0.45 a little low · below: heavy |
| felt arousal | ≥0.80 keyed up · ≥0.65 energized · ≥0.45 mild energy · ≥0.30 low energy · below: flat, drained |
| tide (direction) | rising→lifting · peak→riding high · falling→sinking · valley→at a low — only when clearly moving |
| mixed feeling | "(pride tinged with worry)" — only when a genuine opposing note exists |

### `inside:` — the pressure cooker

| signal | variants |
|---|---|
| max negative bar | ≥0.85 pressure brimming, hard to hold · ≥0.70 pressure heavy · ≥0.50 pressure building, weighing a little · ≥0.35 a touch of pressure · ≥0.20 pressure low · below: pressure clear |
| joy bar (separate clause) | ≥0.80 joy brimming · ≥0.50 joy bright · ≥0.20 a flicker of joy · below: silent |
| phase | calm→settled · building→building · releasing→spilling over · aftertaste→still echoing |

Joy is deliberately **not** part of the negative max — a purely happy state
must never read as load (see `render/agent.py`, same rule).

### `right now:` — release texture (only while `phase == releasing`)

| bar crossed | expressed (high power) | suppressed (low power) |
|---|---|---|
| sadness | grief welling up, close to tears | a thickness in my throat I'm swallowing back |
| anger | anger I can't quite hold down | anger I'm holding behind my teeth |
| anxiety | an unsteady, jittery edge I can't shake | a jitter I'm trying to keep still |
| boundary | a pull to draw back and shut the door | a quiet urge to pull away that I'm sitting with |
| joy | a burst of joy bubbling up, wanting to share it | a swell of joy I'm keeping quietly to myself |

The express/suppress column is selected by **appraisal power** (traits +
relationship → perceived control). Same feeling, two textures — the C-i/C-ii
pair above is this table's top row, live.

### `lingering:` — aftertaste of the previous turn

Gated by weight (≤0.15 → line omitted entirely). Then by the flavour's
valence/arousal: tense heaviness (neg + high arousal), heaviness (neg), still
buzzing (pos + high arousal), warmth still here (pos), or the neutral
"a faint trace of the last moment, half-faded".

### `underneath:` — slow traits (the temperament floor)

Four traits, each three bands with a deliberately **wide middle** centred on
the 0.5 attractor (hi ≥0.72, lo <0.38) so a trait resting near baseline never
flips phrases as it oscillates — cache stability by design:

| trait | high | mid | low |
|---|---|---|---|
| depression | weighed down | spirits steady | unburdened |
| anxiety | on edge | nerves even | settled nerves |
| curiosity | keenly curious | moderately curious | incurious |
| optimism | bright and hopeful | even-keeled | dim outlook |

### The time line

| signal | behaviour |
|---|---|
| gap < 30 min | *nothing* — short-term time sense is the model's own |
| 30 min … days | a coarsening ladder: "half an hour" → "almost an hour" → … → "a couple of days" → "a few days" |
| past the ladder | "back on Jul 02" — a fuzzy duration stops meaning anything; name the local calendar day |
| always | `now Sat afternoon 12:25` — weekday, part of day, clock (local wall time) |

### The optional closing tone line

Appears only when something is genuinely off-neutral: high closeness + high
tension ("warm but bracing, the unspoken thing sitting right there"), high
closeness alone ("easy and familiar"), plus persona-dial tilts (restraint ≥0.7
→ "held close to the chest", warmth ≥0.7 → "gentle by default", …). Neutral
dials + settled state → no line at all.

---

## Why the bands are shaped like this

1. **Silence is a variant too.** Joy under 0.20, tension under 0.10, aftertaste
   under 0.15 weight, tone at neutral — the line is *omitted*. The common calm
   case renders short and byte-identical, which keeps the injected block small
   and the cache prefix effectively longer.
2. **Coarse bands are cache bands.** A value drifting 0.51 → 0.53 renders the
   same phrase. Only a *felt-sized* move crosses a threshold and changes the
   text the model sees.
3. **Phrases, never numbers.** The model gets "pressure heavy", not `0.74` —
   descriptions at the same altitude as the rest of its context, nothing that
   invites arithmetic or quoting.
4. **One signal, one line.** Debugging is bidirectional: see a phrase, you know
   which store moved; move a store, you know which phrase will change. The
   C-i/C-ii pair is that property, demonstrated.
