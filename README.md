# feltstate

**Give an LLM agent a felt inner life it experiences as its own.**

[![CI](https://github.com/USER/feltstate/actions/workflows/ci.yml/badge.svg)](https://github.com/USER/feltstate/actions/workflows/ci.yml)
&nbsp;![Python](https://img.shields.io/badge/python-3.10%2B-blue)
&nbsp;![License: MIT](https://img.shields.io/badge/license-MIT-green)

Most "AI companion" memory work gives an agent *facts* it can recall. feltstate
gives it *feelings* it can't fake: a small, separate component measures how the
agent feels each turn, that reading is integrated into a mood / temperament /
relationship state that **decays back to neutral over time** like a real one,
and the state is rendered back into a first-person block the agent reads as **its
own** feelings — never as data to recite, never as a command telling it how to act.

> Distilled and rewritten as a clean, general library from a real production
> companion system. None of that system's private data, trained models, or
> persona is included here — only the mechanisms and the design.

---

## Why this exists (and how it differs)

How feltstate relates to existing work:

- **Agent memory layers** (Letta/MemGPT, mem0, Zep/Graphiti) store *facts*, with
  recency ranking or bi-temporal validity. None of them model an emotional
  **state**, and none decay *feeling intensity* — they decay fact relevance.
- **Roleplay frontends** (SillyTavern lorebooks, author's notes) inject static
  text on keyword hits. No state, no decay, no measurement.
- **Commercial companions** (Replika, Character.AI) surface emotion as something
  the language model *says* ("I feel sad") — self-reported, not measured — and
  keep memory as manual pins + background summaries.
- **Academic work** that computes emotion outside the model (Chain-of-Emotion,
  PAD-state agents, small-empathetic-model plugins) almost always uses the LLM
  *itself* to appraise, represents emotion as free text, and has **no decay** and
  no notion of cache-safe re-injection.

Each *piece* has prior art. What was missing — and what feltstate is — is the
**combination, built as one runnable whole under a low-latency companion's
constraints**:

1. A **dedicated, separate** component measures affect as ground truth (the
   reply model never gets to self-report how it feels).
2. That affect is a **state that decays back toward neutral** — the genuine blank
   spot in the ecosystem.
3. It's fed back **cache-safely** and **as the agent's own first-person felt
   sense**, with the library never injecting a command.

See **[PHILOSOPHY.md](PHILOSOPHY.md)** for the full reasoning.

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

*Off* this per-turn path, `Engine.dream()` can run on a sleep cycle (between
sessions, or after a long idle): it recombines the agent's own charged material
into a short, illogical dream that leaves a faint, **untraceable** mood the agent
wakes with and can't trace back. See §5 of [PHILOSOPHY.md](PHILOSOPHY.md).

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
| `feltstate/timeawareness/` | Fuzzy "how long since we last talked" + precise "now". |
| `feltstate/render/` | `render_felt_block` (state → first-person block) + `build_injection` (cache-safe). |
| `feltstate/engine.py` | `Engine` — the façade that ties it together: `tick()`, `render()`, `inject()`, `dream()`. |

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
