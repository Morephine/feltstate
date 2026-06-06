# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Evidence-weighted affect on facts** (`feltstate.memory.feeling`, opt-in via
  `Canon.add(..., emotion=...)`): a fact carries a Bayesian `{pos, neg, neu}`
  confidence distribution. Repetition still reinforces *salience* unchanged — but
  a repeated *flat* mention stays neutral (a catch-phrase doesn't masquerade as
  meaning) while a repeated *felt* one settles and gains inertia. `Canon` views
  expose `valence` / `charge` / `entropy`; `MemoryConfig.salience_charge_weight`
  optionally dims emotionally-flat facts in what's shown.
- **Importance-modulated decay curve** (`MemoryConfig.decay_curve="fsrs"`): a
  stretched-exponential whose rate slows with a fact's importance and whose tail
  is fattened for negative-valence facts — low memories linger, bright ones fade.
  Default stays `"linear"`.
- **Negative-channel mood momentum** (`MoodConfig.momentum_mu`, default 0/off): a
  low mood overshoots and recovers slowly (a sulk has a trough), while good moods
  stay on the plain fast EWMA. Carried on the new `Mood.velocity`.
- **`Canon.recall()`** — an agent-called two-stage retrieval tool (metadata
  prefilter → pluggable scorer) with an optional **mood-congruent** re-rank, so a
  low mood surfaces low memories. It returns a list for the agent to use; it never
  injects anything on its own.
- **Bi-temporal history** (`Canon.history` / `Canon.as_of`): a fact carries a
  `valid_at`, and supersede / retract stamp an `invalid_at`, so a belief that
  changed is kept as a timeline rather than erased. `history(keyword)` returns
  every version with its validity window and `active` / `superseded` / `retracted`
  status; `as_of(keyword, when)` returns what was believed true at a past time
  ("last month, what did you think my job was?"). Flat-file, no graph, no infra.
- **Dreams** (`feltstate.dream` + `Engine.dream`): off-the-per-turn-path,
  zero-LLM recombination of the agent's own charged material into a short,
  illogical dream that leaves a faint, *untraceable* mood residue — a feeling
  with its causal thread cut on purpose. Clashing material muddies valence and
  raises arousal; the residue decays through the normal dynamics. Supply rich
  `Fragment`s (or a swapped-in `Phrasebook` for another language); the default
  phrasebook is English.
- **Sleep pressure** (`feltstate.sleep` + `Engine.maybe_dream`): a single
  `Tiredness` accumulator that decides *when* to dream — rises with arousal (an
  intense stretch tires faster than a calm one), gated by a threshold, a 30-min
  idle, and a hard refractory floor, and discharged to zero by a dream. Cadence
  (≈ once a day) emerges from activity, not the clock. A dream's text is also
  dropped once its mood-nudge has decayed, so its lifespan tracks the feeling's.
- **Fact extraction** (`memory.extract`): an optional second-model pass that
  proposes 5W1H facts for a `Canon` — model-agnostic, grey-zone by default, so
  the agent confirms what it keeps.
- **The relationship now evolves** (`affect.relationship`): closeness, trust and
  safety drift with the conversation; tension rises on friction and eases over
  time; repair banks trust capital that never decays.
- **Mood tide** (`affect.tide`): the rising/falling shape of mood, rendered as
  "lifting" / "sinking" / "riding high" / "at a low".
- **Mixed feelings are rendered** ("relieved tinged with sad").
- **Scheduled anticipation**: the joy floor can ramp toward a dated event
  (dopamine pre-payment) instead of staying flat.
- **Top-label hysteresis** (`affect.smooth`): a noisy source no longer flickers
  the rendered block from turn to turn.
- **Companion layer** (`feltstate.companion`): the orchestration + seams that
  turn the engine into a runnable desktop pet. Adapters — `LLMBackend` (reply
  model, with `EchoBackend` / `OpenAICompatBackend`), `FrontendAdapter`
  (avatar/skin), `VoiceAdapter` (TTS), `UserPresenceAdapter`,
  `BehaviorDispatcher`; `companion_turn` (one feel→reply→express→speak round);
  and `CompanionScheduler` — a proactive heartbeat generalised from a production
  companion daemon (pending topics, time windows, focus nudges, random openers,
  bursts, solitude introspection, dreaming, a daily diary), all timing/gating
  configurable via `SchedulerConfig`. `Companion` / `run_companion` tie it
  together; see `examples/companion.py` for a full pet from stub adapters. The
  core engine is unchanged and there are no new dependencies.
- **Memory context expansion** (`feltstate.memory.context`): given a
  transcript and an anchor (a timestamp string -- e.g. a canon entry's `ts`
  -- or an integer index), `get_turn_context` returns the surrounding N turns
  on each side, so a distilled fact can be opened back to the exchange it came
  from. Source-agnostic and read-only: pass your own turns, or use the
  `load_turns` helper. Zero-dependency.

## [0.1.0] - initial

First public release: a clean, dependency-free reference implementation of a
felt inner-state engine for LLM agents.

### Added

- **Affect state** (`feltstate.state`): `AffectState` and its parts — `Mood`,
  `Traits`, `Relationship`, `PressureState`, plus the per-turn `AffectDelta`.
- **Dynamics** (`feltstate.affect`): a multi-bar pressure/release cycle
  (`pressure`), asymmetric hedonic-adaptation trait EWMA (`traits`), and
  symmetric permanent imprints (`imprint`).
- **Memory** (`feltstate.memory`): `Canon`, a decaying 5W1H fact store.
- **Time awareness** (`feltstate.timeawareness`): fuzzy "how long since we
  spoke" + precise "now".
- **Render** (`feltstate.render`): `render_felt_block` (first-person, cache-stable)
  and `build_injection` (cache-safe placement).
- **Sources** (`feltstate.sources`): the `AffectSource` interface, a
  zero-dependency `KeywordSource`, and an OpenAI-compatible `LLMSource`.
- **Engine** (`feltstate.engine`): the `Engine` façade — `tick` / `render` / `inject`.
- README and PHILOSOPHY; two runnable examples; tests across every module;
  ruff + mypy + CI configuration.

[Unreleased]: https://github.com/USER/feltstate/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/USER/feltstate/releases/tag/v0.1.0
