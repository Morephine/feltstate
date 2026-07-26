# feltstate

**Give an LLM agent a mood it can carry forward—and memories that can age, change, and die.**

> Mechanism: persistent affect is estimated by a component separate from the
> reply model, while structured memory is managed through explicit storage,
> recall, provenance, ageing, and deletion tools. This is a prompt/interface and
> state-management design — not a claim about consciousness, subjective
> experience, genuine emotion, or human-like memory.

[![CI](https://github.com/Morephine/feltstate/actions/workflows/ci.yml/badge.svg)](https://github.com/Morephine/feltstate/actions/workflows/ci.yml)
&nbsp;![Python](https://img.shields.io/badge/python-3.10%2B-blue)
&nbsp;![License: MIT](https://img.shields.io/badge/license-MIT-green)
&nbsp;[English](README.md) | [中文](README.zh.md)

feltstate is a **character engine for AI agents** — a small, opinionated
reference library that gives a long-running agent a persistent, *tunable*
character: affect appraised outside the reply model, structured memories that
can fade, strengthen, change, merge, or be retired, and compact state returned
as context rather than behavioural instruction. Around the engine sits an [integration
handbook](#the-integration-handbook) — fifteen chapters on how the pieces
assemble into a companion that speaks, shows a face, works, fails, and stays
itself — with every quoted transcript reproducible from a runnable example.

It draws on affective computing, agent memory, appraisal, and selective
forgetting, but the architecture is its own: affect is appraised outside the
reply model and cannot be authored by it, structured memory carries provenance
through an auditable birth-to-death lifecycle, persisted state moves across
several timescales, and dynamic context is injected without rewriting the static
prompt. Several of these are uncommon in agent libraries — memory that can *die*
with a traceable lineage, a hard ownership boundary on persisted affect, and
off-path zero-LLM dreaming — and they are built that way on purpose, not
reassembled from off-the-shelf parts.

> Distilled and rewritten as a clean, general library, adapted from mechanisms
> used in a private companion prototype. None of that prototype's private data,
> trained models, or persona is included here — only the implementation and
> design choices represented in this repository.

---

## Why this exists

Most agent-memory libraries focus on storing and retrieving text, while many
agent-affect demos reduce mood to a value placed in a prompt. feltstate explores
a narrower but deeper question: **how persistent affect, memory, capability, and
proactive behaviour can change together over the lifetime of a long-running
companion.**

Its main design choices are:

1. **Memory has a lifecycle, not just a search index.** `Canon` stores compact
   5W1H facts whose salience can decay, strengthen through repetition, and last
   longer when recalled. Optional lifecycle tools add birth fingerprints,
   fusion lineage, per-kind ageing clocks, pure death plans, tombstone-first
   deletion, snapshot cleanup, and a hash-linked audit trail for detecting
   unexplained mutation or disappearance.
2. **Affect is estimated separately, not self-reported.** A configured
   `AffectSource` produces the affect signal, so the reply model cannot freely
   author its own persisted state.
3. **State changes across several timescales.** Fast mood, slow temperament,
   relationship, pressure, aftertaste, anticipation, and optional imprints are
   integrated with configurable human-inspired asymmetric dynamics — every
   rate a personality dial rather than a fitted constant (see
   ["Where the numbers come from"](#where-the-numbers-come-from)). The slowest
   layer is **plasticity**: per-bar sensitivity carved by micro hits from
   lived charge, amplifying future inflow, healing toward baseline at a
   safety-paced daily percentage — character change on a ~180-day scale
   (`examples/plasticity.py`).
4. **Capability is judged separately from mood.** An optional skill-memory
   region uses human 1/2/3 ratings, promotion, retirement, and bounded
   exploration rather than letting the reply model declare its own competence.
5. **Proactive behaviour is gated, not merely scheduled.** The reference
   scheduler combines idle time, presence, cooldowns, daily quotas, pending
   topics, time windows, dreams, introspection, and diary behaviours through a
   propose/dispatch/commit flow.
6. **Persistent state is context, never a command.** The reply model receives a
   compact first-person description rather than numeric controls or an
   instruction such as “respond sadly.”
7. **The injection path is cache-aware.** Static persona text can remain a stable
   prefix while dynamic state rides on the latest user message.
8. **Dreaming is an optional state experiment.** Zero-LLM recombination of
   affect-tagged fragments can leave a temporary mood residue without supplying
   the reply model with an explicit causal narrative.

Its contribution is a concrete, inspectable, tested architecture with a specific
ownership boundary between appraisal, persisted state, memory lifecycle, and
reply generation — the parts most companions blur together are here kept
deliberately, and visibly, apart. Each choice above is unpacked, with working
code and real transcripts, in [the integration
handbook](#the-integration-handbook).

### Where the numbers come from

The dynamics constants — EWMA rates, decay curves, pressure thresholds, the
asymmetries — are **personality parameters, not psychological measurements**.
They were hand-tuned against a long-running private deployment the way game
designers tune a movement curve: for a character that stays coherent over
months, not to match a published constant. Every one lives in `config.py`
with a one-line rationale, per-character values ride `PersonaDials`, and the
defaults amount to one reference temperament. Retuning them is not breaking
the model — it is how you write a *different* character: quicker to forgive,
slower to warm, harder to tire. Personality being tunable is the design, not
a disclaimer. Taken together, `config.py` + `PersonaDials` are the **character
configurator** — the dials are how you *write* a character, and the engine is
what keeps that character running. What the numbers are not is fitted to human
data; no such claim is made, and none is needed for the job they do.

### A note on memory fingerprints

The lifecycle package uses SHA-256 fingerprints and a hash-linked ledger for
provenance and tamper evidence. This is **not encryption**, a digital signature,
or protection against an attacker who can rewrite every file and recompute every
hash. Its purpose is to make ordinary lineage, mutation, and deletion auditable
when the ledger is kept as a trusted record.

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
# now Sun morning 9:56
# with you: warming · mostly trusting · mostly safe
# mood: relieved | level, low energy
# inside: pressure clear | settled
# lingering: a faint trace of the last moment, half-faded
# underneath: spirits steady · nerves even · moderately curious · even-keeled

# Feed it back to your reply model — cache-safely — as first-person context:
prompt = eng.inject("so what should we build next?")
# -> your static system prompt stays untouched (and cached); the felt block
#    rides along on the newest user message.
```

Run the full demo:

```bash
python examples/quickstart.py     # pure stdlib, no install needed
```

Want the full desktop-companion assembly — face, voice, heartbeat, memory
tools? Start at [docs/INTEGRATION.md](docs/INTEGRATION.md).

---

## Memory beyond retrieval

The default `Canon` is a flat-file, structured memory store rather than a vector
database. Facts are represented as compact 5W1H records and can be reinforced,
corrected, retracted, queried as-of a past time, recalled through a pluggable
scorer, and expanded back to the transcript context that produced them.

The optional `feltstate.memory.lifecycle` package models a longer path:

```text
source evidence → zero-LLM consistency gate → sealed distilled memory
                → ageing / fusion / lineage → drill back to source context
                → death plan → tombstone → managed-store deletion → audit chain
```

The collector refuses to delete records it cannot trace, living distilled
memories can protect the facts they depend on, and the reaper can remove a dead
record from the live store and explicitly supplied snapshots through a replayable
pending transaction. Source-material rows outside those managed stores are only
marked for deletion; the library does not claim cryptographic erasure.

The new trace path is explicit rather than magical:

- `check_consistency()` is a configurable lexical guardrail for a summary made
  from source rows. It catches unsupported numbers, negation drift, actor drift,
  spliced clauses, inflation, and hollow text without another model call. It is
  **not** a semantic proof; non-space-delimited languages should provide their
  own tokenizer and language tables.
- `smelt()` combines that gate with born salience and a provenance fingerprint.
  It rejects unsealed output by default; callers may explicitly opt into an
  unsealed fallback.
- `drill()` walks `src` and fusion `lineage` back through caller-owned memory
  storage. `leaf_pointers()` keeps raw evidence even when only part of the
  genealogy survives. `trace_contexts()` resolves each pointer's full `t0`–`t1`
  range into transcript turns and can optionally verify exact source text.
- `trace_memory()` joins the tree, leaf evidence, transcript ranges, affect trail,
  lost-branch count, and optional source-hash verification into one report.
- `verify_source_ptr()` checks exact source text against the pointer hash. It
  does not restore deleted text, locate files automatically, or turn a hash into
  encryption.

```python
from feltstate.memory.lifecycle import trace_memory

report = trace_memory(
    memory_fp,
    fingerprint_store.get,
    transcript_loader,
    before=3,
    after=3,
    load_source_text=exact_source_text_loader,  # optional hash verification
)
```

`Canon`, lifecycle fingerprints, and transcript storage remain composable
pieces rather than a hidden automatic pipeline. A memory is only fully
traceable when the application keeps its fingerprint, the referenced source
archive, and a resolver/loader for those stores.

```bash
python examples/memory_lifecycle.py
```

---

## How affect works

```
            ┌─────────────┐   independently estimated (not self-report)
 messages → │ AffectSource │ ──────────────► AffectDelta (this turn's estimate)
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

The reply model uses the felt block as additional context when generating a
reply. The library never writes "be sad now" into the prompt — it only supplies
state.

### A fine-tuned source

`KeywordSource` and `LLMSource` are the two example sources shipped with the
core. An optional third source, `feltstate.sources.vheart.VheartSource`, loads a
LoRA adapter from the Hub. Two small experimental adapters are referenced:
[`kaishuiji/vheart-affect-v8`](https://huggingface.co/kaishuiji/vheart-affect-v8)
on a 1.5B base and
[`kaishuiji/vheart-affect-v9`](https://huggingface.co/kaishuiji/vheart-affect-v9)
on a 4B base.

Treat these adapters as interface demos — closer to research toys than
production classifiers. Their training data is not released, there is no public
reproducible benchmark in this repository, and no accuracy or suitability claim
is made. They are useful for trying the integration path, not as a model
recommendation.

```bash
pip install "feltstate[vheart]"
```

```python
from feltstate import Engine
from feltstate.sources.vheart import VheartSource

eng = Engine(source=VheartSource("kaishuiji/vheart-affect-v9"))
```

Constructing `VheartSource` downloads the base model and the adapter and
loads both onto the GPU (or CPU), which is several gigabytes and a
visible startup pause. Download, load and network failures during
construction propagate. After construction, `read()` itself never raises
— tokenizer, generation and parse failures collapse to a low-confidence
neutral reading.

*Off* this per-turn path, the agent **dreams**: `Engine.maybe_dream()` fires only
when a single sleep-pressure accumulator (driven by arousal, not the clock) says
it's tired enough — then recombines stored, affect-tagged fragments into a short,
illogical dream that leaves a faint mood residue whose causal thread is not
surfaced to the reply model as an explicit cause, which decays over the next
hours like any feeling. See §5 of
[PHILOSOPHY.md](PHILOSOPHY.md).

---

## Layout

| Module | What it is |
|---|---|
| `feltstate/state.py` | The schemas: `AffectState`, `AffectDelta`, `Mood`, `Traits`, `Relationship`, `PressureState`. Plain dataclasses, JSON round-trip. |
| `feltstate/config.py` | Every tunable in one place (EWMA rates, decay, pressure thresholds, label maps) + `PersonaDials`. |
| `feltstate/sources/` | `AffectSource` interface + `KeywordSource` (rules, zero-dep) + `LLMSource` (any OpenAI-compatible endpoint). The pluggable affect-estimation seam. |
| `feltstate/affect/` | The dynamics: `pressure` (multi-bar release), `traits` (asymmetric adaptation), `imprint` (permanent marks), `relationship` (the bond evolving), `tide` (mood's rise & fall), `smooth` (label hysteresis). |
| `feltstate/memory/` | `Canon` — a decaying 5W1H fact store; `feeling` — optional evidence-weighted affect per fact; `recall` and bi-temporal history; `extract` and `context`; `skill` — a human-rated capability region; `lifecycle` — optional provenance fingerprints, lineage, ageing clocks, death planning, tombstone-first deletion, snapshot cleanup, and a hash-linked audit ledger. |
| `feltstate/dream.py` | Off-path, zero-LLM: recombines the agent's charged material (`Fragment`s) into an *illogical* dream that leaves a faint mood residue not surfaced to the reply model as an explicit cause. Swap the `Phrasebook` for another language. |
| `feltstate/sleep.py` | The single sleep-pressure accumulator (`Tiredness`) that decides *when* to dream: rises with arousal, gated by threshold + idle + a hard refractory floor, discharged by a dream. Homeostatic, not clock-driven. |
| `feltstate/timeawareness/` | Fuzzy "how long since we last talked" + precise "now". |
| `feltstate/render/` | `render_felt_block` (state → first-person block) + `build_injection` (cache-safe). |
| `feltstate/engine.py` | `Engine` — the façade that ties it together: `tick()`, `render()`, `inject()`, `dream()`, `maybe_dream()`. |
| `feltstate/companion/` | A reference orchestration layer: `LLMBackend` / `FrontendAdapter` / `VoiceAdapter` / `UserPresenceAdapter` seams, `companion_turn` for one estimate→reply→express→speak round, and `CompanionScheduler` for optional proactive behaviours. |

---

## The companion loop

The core engine presents an agent with a persistent affective state in
first-person form; `feltstate.companion` provides a *reference companion
skeleton*. Implement two adapters — a `FrontendAdapter` (your avatar/skin) and
a `VoiceAdapter` (your TTS) — bring an `AffectSource`, a reply `LLMBackend`,
and a persona, and `Companion` wires the rest: a foreground `say()` turn (feel →
reply → express → speak) and a `CompanionScheduler` heartbeat that checks configured timing and gates for
optional proactive speech, introspection, dreams, or diary writes —
all the timing and gating adapted from mechanisms used in a private companion
prototype, with the endpoints and prompts left to you.

```bash
python examples/companion.py       # runnable stub companion — no deps, no network
python examples/companion_live.py  # interactive: a real heartbeat, proactive
                                   # topic raises, memory that survives restart
```

---

## The integration handbook

The engine is the smaller half of building a companion; the larger half is
integration — what goes in the prompt and in what order, how a reply becomes a
face and a voice, what the user sees while the agent works, fails, or thinks.
The `docs/` chapters cover that half as concrete patterns over this library's
actual seams. They describe one coherent way to assemble a companion — the
shape used by the private reference implementation this library was distilled
from — not the only way. Transcripts quoted in them are real output of the
runnable examples.

| chapter | what it covers |
|---|---|
| [INTEGRATION](docs/INTEGRATION.md) | the assembly manual: wiring diagram, prompt partition and its cache economics, heartbeat duties, the proactive path, adapter swaps, the shell / bridge / soul layering, the privacy boundary |
| [PROMPT_STACK](docs/PROMPT_STACK.md) | the static/dynamic partition, sandwich ordering, and the forget probe — persona upkeep that costs nothing until it's needed |
| [PROMPT_SHAPES](docs/PROMPT_SHAPES.md) | one neutral persona, three prepared moments as full message arrays; the variant master table from state bands to phrases |
| [STYLE_SPECTRUM](docs/STYLE_SPECTRUM.md) | optional delivery notes — how a feeling holds a pen: form, never content |
| [OUTPUT_CHAIN](docs/OUTPUT_CHAIN.md) | reply → face and voice: two signal channels, first-sentence TTS streaming, renderer portability down to a hotkey bridge |
| [AGENT_WORK_UX](docs/AGENT_WORK_UX.md) | narrating long agent work without breaking character: canned voicebanks, the narration throttle, cross-turn work |
| [FAILURE_IN_CHARACTER](docs/FAILURE_IN_CHARACTER.md) | two audiences, two truths: felt failure kinds, the watchdog case, recovery etiquette |
| [BRIDGE_ETIQUETTE](docs/BRIDGE_ETIQUETTE.md) | being a person over a chat platform: receipts, typing, attachments, and the emergency command lane |
| [INTERRUPTION](docs/INTERRUPTION.md) | being cut off gracefully: no-headphone barge-in, the stop chain, the recovery posture |
| [PERCEPTION](docs/PERCEPTION.md) | images and screens as input: persist → perceive → reply, and the pull eye |
| [INNER_LIFE](docs/INNER_LIFE.md) | the silent thinking channel, the face that moves between turns, and the self-correction round |
| [MULTI_PERSON](docs/MULTI_PERSON.md) | one soul, many people: per-speaker relationship keying and the zero-pollution rule |
| [GAME_SHELL](docs/GAME_SHELL.md) | the game as a third surface: minute-scale director intents vs second-scale engines, the sight gate, and driving It Takes Two |
| [MEMORY_TOOLS](docs/MEMORY_TOOLS.md) | Canon as five function-calling tools, with a real bi-temporal trace |
| [PHILOSOPHY](PHILOSOPHY.md) | why persisted state is described to the model and never commanded |

The matching runnable examples, all deterministic or offline:

```bash
python examples/prompt_shapes.py    # the three moments, full message arrays
python examples/memory_tools.py     # the five tools + dispatcher, end to end
python examples/agent_narration.py  # voicebank pools, throttle, failure lines
python examples/style_spectrum.py   # state bands → delivery notes
python examples/companion_live.py   # the interactive loop (FELTSTATE_LIVE_FAST=1 to hurry it)
python examples/game_director.py    # the game shell: intents, sight gate, one running mouth
python examples/maze_game/play.py   # the director shape as a playable maze (WASD; offline stub,
                                    # or any OpenAI-compatible model via MAZE_LLM=1)
```

---

## Scope — what this is and isn't

- **Is:** a clean, runnable *reference implementation* of the ideas, dependency-
  free at the core. Bring your own `AffectSource`, persona text, and a place to
  store state.
- **Isn't:** a finished product. There is no bundled personality, no trained
  affect classifier, and no conversational data, avatar, or TTS. The
  `feltstate.companion` package is a reference orchestration skeleton, not a
  complete pet application. See `examples/companion.py` for a stubbed demo and
  `examples/companion_live.py` for the interactive one.
- The default `KeywordSource` is intentionally crude. `LLMSource` is still an
  estimate produced by another model call, and the optional Vheart adapters are
  experimental demos rather than validated classifiers.
- The default `Canon` reloads its flat file per operation (O(n), lexical
  scoring) — right-sized for one companion's thousands of distilled facts,
  where an auditable flat file beats an opaque database and decay/compaction
  keep the live set small. Fleet-scale corpora or semantic search need a real
  store behind the same interface.

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
