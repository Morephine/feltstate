# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Dreams** (`feltstate.dream` + `Engine.dream`): off-the-per-turn-path,
  zero-LLM recombination of the agent's own charged material into a short,
  illogical dream that leaves a faint, *untraceable* mood residue — a feeling
  with its causal thread cut on purpose. Clashing material muddies valence and
  raises arousal; the residue decays through the normal dynamics. Supply rich
  `Fragment`s (or a swapped-in `Phrasebook` for another language); the default
  phrasebook is English.
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
