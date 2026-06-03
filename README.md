# feltstate

**Give an LLM agent a felt inner life it experiences as its own.**

[![CI](https://github.com/USER/feltstate/actions/workflows/ci.yml/badge.svg)](https://github.com/USER/feltstate/actions/workflows/ci.yml)
&nbsp;![Python](https://img.shields.io/badge/python-3.10%2B-blue)
&nbsp;![License: MIT](https://img.shields.io/badge/license-MIT-green)

feltstate is a small, opinionated reference implementation of one idea: a
companion that stays the **same someone** over a long relationship. It gives an
agent a felt inner state it experiences as its own but cannot author at will —
affect *measured* each turn by a component separate from the reply model,
integrated into a mood / temperament / relationship state that **decays like a
real one** (good moods fade fast, bad ones linger), and handed back as a
first-person feeling the agent reads as **its own** — never as data to recite,
never as a command telling it how to act. It can even *dream*: leave itself a
faint mood it can't trace back.

None of the individual mechanisms are new — see **[PHILOSOPHY §7](PHILOSOPHY.md)**
for the 2024–2026 prior art, named honestly. The rare thing is the **coherence**:
every piece serving that one goal, and the lines it refuses to cross to get there.

> Distilled and rewritten as a clean, general library from a real production
> companion system. None of that system's private data, trained models, or
> persona is included here — only the mechanisms and the design.

---

## Why this exists (and how it differs)

By 2026 the field has independent versions of most pieces here — a dedicated
affect estimator ([co-r-e](https://arxiv.org/abs/2601.16087)), a decaying mood
state with momentum (REMT, [PSYA](https://arxiv.org/abs/2507.19495)), the
separate appraisal step ([Chain-of-Emotion](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0301033)),
even dreams for companions ([2601.06115](https://arxiv.org/abs/2601.06115)). So
feltstate is **not** a claim to have invented affective computing. It is the
**coherent assembly + the discipline** — and no surveyed system, research or
product, holds all of these at once:

- **Affect is measured, not self-reported.** A separate component appraises the
  *agent's own* state (most estimators read the *user*); the reply model can't
  flatter itself into a mood.
- **It decays — asymmetrically.** Good moods fade fast, bad ones linger. The
  agents that model decay tune it the *opposite* way (damping negativity for
  stability); feltstate sustains it, because that asymmetry is what makes a
  temperament rather than a mood ring.
- **It's handed back as the agent's own feeling, never as a command.**
  First-person identity-merge — not a `valence=-0.3` readout, not "respond in a
  sad tone."
- **It can dream** — a zero-LLM recombination that leaves a small, deliberately
  *un-traceable* mood, so the agent can wake a little off with no cause it names.
- **It's cache-safe**, so a persistent always-on companion isn't prohibitively
  expensive to run.

The honest pitch is not "novel mechanisms." It is "the cleanest opinionated whole
— measured, decaying, identity-merged, never commanding — while that combination
is still rare." See **[PHILOSOPHY.md](PHILOSOPHY.md)** for the full reasoning and
the prior-art map.

---

## Quickstart

```python
from feltstate import Engine, KeywordSource

# KeywordSource is a zero-dependency, rule-based reference source — good enough
# to see the loop work. Swap in LLMSource (any OpenAI-compatible endpoint) or
# your own fine-tuned classifier for real use.
eng = Engine(source=KeywordSource(), state_path="state.json",
             persona="a dry-humoured, loyal friend")

eng.tick([{"role": "user", "content": "I finally shipped it!! couldn't have done it without you"}])

print(eng.render())
# [how I feel right now]
# close · trusted · mostly safe · no friction
# curious, content | warm, mild energy
# pressure low, joy bright | building
# ...

# Feed it back to your reply model — cache-safely — as the agent's own sense:
prompt = eng.inject("so what should we build next?")
# -> your static system prompt stays untouched (and cached); the felt block
#    rides along on the newest user message.
```

Run the full demo:

```bash
python examples/quickstart.py     # pure stdlib, no install needed
```

---

## How it works

```
            ┌─────────────┐   measures (ground truth, not self-report)
 messages → │ AffectSource │ ──────────────► AffectDelta (this turn's reading)
            └─────────────┘                        │
                                                    ▼
   ┌──────────────────── Engine.tick() integrates over time ───────────────────┐
   │  traits    asymmetric EWMA — good moods fade fast, bad ones linger          │
   │  mood      felt valence/arousal, pulled toward what traits imply            │
   │  pressure  5 bars (sadness/anger/anxiety/boundary/joy) fill, cross a        │
   │            threshold, *release*, then settle — they don't stay maxed        │
   │  imprint   optional: deep moments leave permanent marks (symmetric:         │
   │            both wounds and warmth, so the agent doesn't only scar)          │
   └────────────────────────────────────────────────────────────────────────────┘
                                                    │  persisted AffectState
                                                    ▼
            ┌─────────────┐  render_felt_block + time sense (fuzzy "how long
 reply  ◄── │ render/inject│  since we talked", precise "what time is it now")
 model      └─────────────┘  → first-person block, injected cache-safely
```

The reply model reads the felt block and **decides for itself** how to act. The
library never writes "be sad now" into the prompt — it only ever supplies state.

*Off* this per-turn path, the agent **dreams**: `Engine.maybe_dream()` fires only
when a single sleep-pressure accumulator (driven by arousal, not the clock) says
it's tired enough — then recombines the agent's own charged material into a short,
illogical dream that leaves a faint, **untraceable** mood it wakes with and can't
trace back, which decays over the next hours like any feeling. See §5 of
[PHILOSOPHY.md](PHILOSOPHY.md).

---

## Layout

| Module | What it is |
|---|---|
| `feltstate/state.py` | The schemas: `AffectState`, `AffectDelta`, `Mood`, `Traits`, `Relationship`, `PressureState`. Plain dataclasses, JSON round-trip. |
| `feltstate/config.py` | Every tunable in one place (EWMA rates, decay, pressure thresholds, label maps) + `PersonaDials`. |
| `feltstate/sources/` | `AffectSource` interface + `KeywordSource` (rules, zero-dep) + `LLMSource` (any OpenAI-compatible endpoint). The pluggable "how does it feel?" seam. |
| `feltstate/affect/` | The dynamics: `pressure` (multi-bar release), `traits` (asymmetric adaptation), `imprint` (permanent marks), `relationship` (the bond evolving), `tide` (mood's rise & fall), `smooth` (label hysteresis). |
| `feltstate/memory/` | `Canon` — a decaying 5W1H fact store (intensity fades, repetition reinforces, recall slows decay); `extract` — optional second-model fact extraction into it. |
| `feltstate/dream.py` | Off-path, zero-LLM: recombines the agent's charged material (`Fragment`s) into an *illogical* dream that leaves a faint, **untraceable** mood residue. Swap the `Phrasebook` for another language. |
| `feltstate/sleep.py` | The single sleep-pressure accumulator (`Tiredness`) that decides *when* to dream: rises with arousal, gated by threshold + idle + a hard refractory floor, discharged by a dream. Homeostatic, not clock-driven. |
| `feltstate/timeawareness/` | Fuzzy "how long since we last talked" + precise "now". |
| `feltstate/render/` | `render_felt_block` (state → first-person block) + `build_injection` (cache-safe). |
| `feltstate/engine.py` | `Engine` — the façade that ties it together: `tick()`, `render()`, `inject()`, `dream()`, `maybe_dream()`. |

---

## Scope — what this is and isn't

- **Is:** a clean, runnable *reference implementation* of the ideas, dependency-
  free at the core. Bring your own `AffectSource`, persona text, and a place to
  store state.
- **Isn't:** a drop-in companion. There's no bundled personality, no trained
  model, no conversational data — those are yours to supply. The companion
  system this was extracted from was deeply tied to one specific character and
  user; that coupling — and all private data — was deliberately left out.
- The default `KeywordSource` is intentionally crude. The interesting affect
  signal comes from `LLMSource` or a model you fine-tune for the job.

---

## Install

```bash
pip install -e .          # core is pure standard library
pip install -e ".[dev]"   # + pytest, ruff, mypy
```

Requires Python 3.10+.

## Development

```bash
ruff check .          # lint
ruff format .         # format
mypy feltstate        # type check
pytest -q             # tests
```

All four run in CI (`.github/workflows/ci.yml`) on Python 3.10–3.13. See
[CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

## License

MIT — see [LICENSE](LICENSE).
