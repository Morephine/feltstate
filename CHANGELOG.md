# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Add a zero-LLM distilled-memory consistency gate (`memory.lifecycle.consistency`)
  covering lexical support, foreign-clause splicing, invented numbers, negation
  drift, length inflation, hollow summaries, and optional actor-attribution
  checks. Tokenisation and language tables are configurable; this is a guardrail,
  not semantic verification.
- Add `smelt()`: a pure consistency → salience → fingerprint pipeline for
  caller-produced distilled memories. Provenance is required by default, with an
  explicit opt-out for unsealed fallback records.
- Add genealogy drilling and source-context resolution (`drill`,
  `leaf_pointers`, `affect_trail`, `trace_contexts`, `trace_memory`) so a stored
  fingerprint can be walked through `src` / fusion lineage to raw source
  pointers, resolved across the complete `t0`–`t1` transcript range, and
  optionally checked against exact archived source text.
- Add `verify_source_ptr()` for exact-text verification against a sealed source
  hash. New source pointers retain the complete SHA-256 digest; verification
  remains compatible with early 16-character alpha pointers.

### Changed

- Reorder the public project narrative around its strongest implemented
  capabilities: provenance-aware memory lifecycle, appraisal/generation
  separation, multi-timescale affect, human-rated skill memory, and gated
  proactive behaviour. Remove blanket claims that the project has no novel or
  distinctive pieces while retaining explicit limits on scientific and security
  claims.
- Clarify that memory fingerprints and the hash-linked ledger provide
  provenance and tamper evidence, not encryption or digital signatures.
- Tighten public wording around affect estimation: model and rule outputs are
  estimates, not ground truth or psychological measurements. The README keeps a
  concise product hook while explicitly avoiding consciousness claims.
- Reframe the optional Vheart adapters as experimental interface demos — closer
  to research toys than production classifiers — with no public benchmark or
  suitability claim.

### Fixed

- Preserve source evidence from a partially-pruned genealogy: a fused memory no
  longer loses the copied pointers belonging to one missing branch merely
  because another branch still resolves.
- De-duplicate provenance pointers by their full `(file, t0, t1, sha)` identity
  instead of collapsing identical text observed at different times.
- Make fingerprint verification fail closed on schema-invalid cores even if an
  invalid core is accompanied by a recomputed matching hash.
- Reject empty-source distillation and invalid provenance by default; clamp born
  salience to a valid non-negative range.
- Include complete 5W1H fields in consistency evidence, use word-boundary
  negation checks, and compare configured self names case-insensitively.
- Quarantine malformed Canon JSONL rows and valid non-object JSON values as
  structured evidence without duplicating the same quarantine record on every
  read.
- Reject malformed source-hash fields and prevent the explicitly unsealed
  `smelt()` fallback from admitting invalid identity, affect, or UTC metadata.

- Serialise concurrent ``Companion.say()`` coroutines with a per-event-loop
  ``asyncio.Lock`` in addition to the cross-thread ``RLock``, preventing
  overlapping voice/frontend calls and shared-history mutations.
- Keep each scheduler thread bound to its own stop event; a timed-out ``stop()``
  now retains the live thread handle and suppresses duplicate ``start()`` calls
  until the original heartbeat exits. ``stop(timeout=...)`` reports success.
- Validate persisted state roots as JSON objects. Corrupt scheduler state and
  engine sidecars are quarantined to timestamped siblings before safe defaults
  are used, preserving the original bytes for diagnosis or recovery.

## [0.2.0a1] - 2026-07-13

**Experimental pre-release.** Prepared for the 0.2.0a1 pre-release. The public
API and the affect/memory dynamics may still change between alphas. Pin the
exact version if you depend on current behaviour.

### Added

- **Memory lifecycle** (`feltstate.memory.lifecycle`): four small organs for
  provenance and honest forgetting — `fingerprint` (each memory is born with a
  sealed source + affect + UTC birth record, hashed into a verifiable id),
  `clocks` (per-kind decay with no floor and only declared immortality), `gc`
  (a pure judge that returns a death plan; authority flows downhill and the
  unfingerprinted can never be collected), `reaper` (the executioner —
  tombstone-first physical deletion across the live store and its snapshots,
  with a replayable pending ledger for crash recovery), and `chain` (a
  hash-linked ledger that tells a lawful, tombstoned death from a silent
  evaporation). Source-material rows are marked for deletion, not yet
  physically purged (documented as future work).
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
  illogical dream that leaves a faint mood residue not surfaced to the reply
  model as an explicit cause — its causal thread cut on purpose. Clashing material muddies valence and
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
  and `CompanionScheduler` — a proactive heartbeat adapted from a private companion
  prototype (pending topics, time windows, focus nudges, random openers,
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

### Security

- **Endpoint URL allow-listing**: every caller-supplied `base_url`
  (`LLMSource`, `LLMFactExtractor`, `OpenAICompatBackend`) is now validated at
  construction to be an `http`/`https` URL with a host. Other schemes
  (`file://`, `ftp://`, `gopher://`, ...) are rejected with a clear `ValueError`
  before anything reaches `urllib`. Scheme/host check only — not SSRF defence.
- **Pinned model revisions**: `VheartSource` gained `revision=` (adapter,
  default `"main"`) and `base_revision=` (base model) parameters, threaded to
  the Hub downloads. Pin an immutable commit for reproducible, supply-chain-safe
  loads; a new `THIRD_PARTY_NOTICES.md` documents the referenced model + base
  and their licences.
- **Sanitised model-output labels**: `VheartSource` labels and `mixed_blend`
  names are bounded to short, single-line, alphanumeric-ish tokens; anything
  with newlines, control characters, or markup-breaking punctuation is dropped,
  so model output cannot inject arbitrary text into a downstream prompt.
- Expanded `SECURITY.md` threat model (state-at-rest, hosted-endpoint data
  exposure, prompt injection, concurrency/crash recovery, deletion guarantees,
  weight supply chain, presence-probe fail-open).

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

[Unreleased]: https://github.com/Morephine/feltstate/compare/v0.2.0a1...HEAD
[0.2.0a1]: https://github.com/Morephine/feltstate/compare/v0.1.0...v0.2.0a1
[0.1.0]: https://github.com/Morephine/feltstate/releases/tag/v0.1.0
