# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `docs/GAME_SHELL.md` + `examples/game_director.py`: the game as a third
  surface. The identity flip (she is the space, or the second player — never
  a bolted-on NPC), the constitution stated from its physical premise
  (**model latency is seconds-to-minutes and not solvable**; minute-scale
  intents vs second-scale engines is the consequence), prose-summary senses
  with a pull-eye god view (never coordinate dumps), talking as the control
  surface (intent extracted from her words — meddling as an extension of
  banter), the pending-intent queue with a sight gate (plus per-action gate
  exemption: being seen to act is sometimes the act), the
  don't-get-caught research (renderer culling flags lie, raycast + margin,
  peripheral vision, ~56% unnoticed-change findings), and the It Takes Two
  ladder as played: virtual-gamepad tool daemon → process-memory telemetry →
  a UE4SS bridge borrowing the game's own follow capability. Three maturity
  tiers stated honestly (built-and-played / designed / written route). The
  example runs the full loop offline and deterministic — four director
  beats, every change landing out of sight on the engine's clock.

### Changed

- **The milestone tuning table moved out of logic into config**
  (`MILESTONE_EFFECTS` + `MILESTONE_ALIASES` in `feltstate.config`, same idiom
  as `LABEL_TO_PRESSURE`): `_apply_milestone` was a wall of hardcoded
  constants (0.025, 0.30·sev, …) — a tuning table pretending to be code. It
  is now a small interpreter over the config table; numbers unchanged, pinned
  by tests.
- **The anticipation joy floor's magic 0.018 is now two named knobs**
  (`anticipation_gain`, `anticipation_ref_decay`): the old formula divided by
  a bare 0.018, silently re-scaling anticipation whenever `idle_decay` was
  retuned. The coupling itself is intentional (a floor below per-tick cooling
  evaporates the same tick) — it is now documented, and setting
  `ref_decay = idle_decay` pins the floor absolute. Defaults reproduce the
  old value bit-for-bit.
- **`render()`/`inject()` accept an injectable clock** (`now=`), mirroring
  `tick()` — the render side of the engine was untestable against a fixed
  wall clock while the tick side wasn't.
- **Canon degrades loudly, not silently, where `flock` is unavailable**: the
  cross-process lock attempt was `except Exception: pass` around an inline
  `import fcntl` — on Windows every write silently ran without cross-process
  locking. The module now resolves `fcntl` once at import, warns exactly once
  when falling back to in-process locks, and narrows the guards to `OSError`.
- **Canon's scale envelope documented honestly** (module docstring + README
  scope): every operation is an O(n) full-file load with lexical scoring —
  right-sized for one companion's thousands of distilled facts, not for
  fleet-scale corpora or semantic search; swap a real store behind the same
  interface past that point.
- Reframed how the dynamics constants present themselves (README "Where the
  numbers come from", the matching Chinese section, PHILOSOPHY §4): they are
  personality parameters tuned for character coherence over long-running use
  — exposed as dials (`config.py`, `PersonaDials`), with the defaults forming
  one reference temperament — rather than an apology for not being fitted to
  human data. Tunability is the design; the no-psychological-claim honesty is
  unchanged. Also mends a broken sentence the old PHILOSOPHY paragraph
  carried.

## [0.2.0a2] - 2026-07-18

The integration-handbook release: thirteen `docs/` chapters and five new
runnable examples covering how the engine assembles into a full companion —
plus a hardening pass (concurrency, NaN, timezone, decay-anchor fixes) pinned
by a dedicated regression suite.

### Fixed

- **The return-after-a-gap line could never render on the return turn**:
  `tick()` re-anchored the last-contact clock before `inject()` read it, so
  "3 days since we last spoke" only ever appeared on later proactive renders —
  never on the turn where the user actually came back. The felt gap is now
  captured at anchor time and preferred by `render()` while the live gap is
  under the gate.
- **Imprint decay was quadratic in age**: the decay anchor never advanced, so
  every `decay_imprints` call re-charged the entire window since the event — a
  per-tick loop drained "two to three years to floor" of vividness in days. A
  dedicated `last_decay_ts` clock is stamped each pass, making decay
  frequency-invariant; legacy imprints still pay their full absolute age once.
- **NaN/Infinity readings clamped to the extreme bound**: `max(lo, min(hi, nan))`
  evaluates to `hi`, so a model answering `NaN` (which `json.loads` accepts, and
  `float("NaN")` parses) produced a maximal fully-trusted affect delta —
  injectable via chat. `LLMSource`/`VheartSource` coercion and clamps now reject
  non-finite values and fall back to neutral defaults.
- **Injected "now" was rendered in UTC**: `Engine.render()` fed a UTC datetime
  into `now_phrase`, whose contract is caller-local wall clock — on a UTC+8 host
  every felt block carried a weekday/part-of-day/clock line up to a day off.
  Gap arithmetic stays in UTC; display converts to local time. The `back on
  {Mon DD}` fallback likewise names the local calendar day.
- **Mixed naive/aware timestamps crashed or silently corrupted time features**:
  `time_since_phrase` raised `TypeError` past its parse guard (killing the
  caller); `Tiredness.rise` swallowed the same error as "no elapsed time" while
  advancing the stamp (eating accrued sleep pressure); `hours_since_dream` read
  it as "never dreamed" (bypassing the refractory floor). All three now coerce
  legacy stamps into the caller's frame.
- **Post-aftertaste settle conjured phantom charge**: `floor + (cur-floor)*keep`
  pulled sub-floor bars *up*, so a sadness release materialised joy/anger from
  nothing and pre-loaded the next build-up. Settling now only moves bars down.
- **Agent-scale readout counted joy as load**: `render_agent_feeling` took its
  band from `max()` over all bars with negative-only phrasing — a purely happy
  agent read as "worn down and tense". Joy is excluded, mirroring the felt-block
  aggregation.
- **`feltstate.companion` failed to import on Windows**: the topics store's
  concurrency fix introduced a top-level `import fcntl`. Locking now degrades to
  a per-process `threading.Lock` where `flock` is unavailable.
- **Canon read-modify-write races could erase records**: mutators loaded
  *outside* the per-path lock and rewrote inside it, so an `add()` racing a
  recall-bump/rating could be wiped by the loser's stale snapshot — the exact
  failure the lock's header comment promised away. The lock is now reentrant
  (thread `RLock` + flock depth counter) and held across the whole transaction
  in `Canon` (`add`/reinforce, recall bumps, `confirm`, `correct`, `retract`,
  `compact`) and the skill region (`record_rating`, `recall_skills`).
- **`compact()` had a crash window that destroyed archived-tier facts**: the
  main store was rewritten (dropping dim facts) *before* they were appended to
  the archive sidecar; a crash between the two writes lost them from both files.
  Compaction now lands the archive first — idempotent on retry via main-wins
  dedup.

### Changed

- `README.md`: the integration handbook surfaced — a fourteen-chapter table
  (thirteen `docs/` chapters + PHILOSOPHY) with the deterministic/offline
  example commands beside it, the companion-loop section pointing at both the
  stub and the interactive example, and intro/design-choice pointers into the
  handbook. Framing kept factual: the chapters describe one coherent assembly
  shape — the one the private reference implementation uses — not the only
  way.

### Added

- `README.zh.md`: full Chinese translation of the README, kept
  section-for-section with the English one; language switcher linked from
  both.
- `docs/MULTI_PERSON.md`: one soul, many people — the relationship-keying
  pattern. The invariant (mood/pressure/traits and Canon are singular;
  `Relationship` is one *per pair* — its five axes, `repair_history`
  included, are properties of a pair and not transferable), the mechanical
  keying flow on `Relationship.to_dict`/`from_dict` (stable platform ids,
  never display names), the zero-pollution rule (no speaker identity → no
  ledger: introspection, frontend and game fires write to no one's account;
  unknown speakers never default onto the mainline key), memory staying
  whole (per-person recall is a filter over one store via Canon's `who`,
  with rendering discretion on top), and what the felt block changes per
  speaker: relationship lines only — the stranger meets the same person on
  a different footing.
- `docs/INTEGRATION.md` §8: the shell / bridge / soul layering — the
  promised body/bridge/soul write-up. One soul (engine + stores, exactly
  one), swappable shells (a body at home), addable bridges (a phone), state
  continuous across surfaces with nothing "logging in"; the home
  (face-to-face) vs away (texting her) experience contrast; a game-director
  shell as the third surface proving the shape generalizes — give her the
  world and let her decide, never pipe bars into world rules; and the
  per-surface visibility law as one table (rendered first-person output
  crosses; stores and numbers never do).
- `docs/PERCEPTION.md`: input-side multimodality — the layering rule
  (perception is input, not state: images move her through appraisal of the
  conversation, never through a direct affect write), the inbound
  persist→perceive→reply path for both multimodal backends (content array
  with `render()` assembled by hand) and caption bridges (descriptions framed
  as "seen, not heard"), and the pull-eye pattern: an always-fresh local file
  she *chooses* to look at — looking as an act, cost under control, frames
  treated as the most sensitive artifact in the system;
  `FrontendAdapter.read_screen()` documented as the ask-side seam ("the
  application decides how; feltstate only asks — default: no eyes").
- `docs/INNER_LIFE.md`: three patterns that make idle time real. **The silent
  thinking channel** — what happens behind the thinking lamp: the library's
  half (`IntrospectSource`: quota-free, solitude-gated, once per window per
  day, retried on failed delivery) and the app's half, a full turn fully
  swallowed (output to a sink, never the voice; only a thinking/idle status
  crosses to the skin; the thought lands in diary/Canon, the state ticks, and
  it seeps into the next felt block unannounced). **The expression direct
  push** — extending the turn-edge face channel to plain heartbeat ticks:
  `expression_signal` is a pure function of (prev, new) and the adapter owns
  the throttle, so a drifting state reaches the face with zero tokens —
  dedupe before throttle or the face strobes. **The self-correction round** —
  the slow half of self-maintenance pairing the forget probe's fast reflex: a
  solitude-gated swallowed turn over her own recent replies, judging voice
  not facts, sinking to notes-to-self and an expiring nudge line — never an
  apology, never a permanent instruction.
- `docs/BRIDGE_ETIQUETTE.md`: being a person over a chat platform — the fixed
  emoji receipt vocabulary (working / done / deliberate-silence / queue-full)
  with the four rules that keep receipts trustworthy, the typing indicator as
  the bridge's thinking lamp, attachment in/out etiquette, and the emergency
  command lane: a plain-text `!` control plane parsed by the bridge process
  itself — alive when the model is the problem, terse by design, reducing
  operations only.
- `docs/INTERRUPTION.md`: being cut off gracefully — no-headphone voice
  barge-in (the echo problem and its solutions ladder), the text stop chain
  (playback → synthesis queue → in-flight turn → pending narration, idempotent,
  acknowledged by the lane not the character), stop-*talking* vs stop-*working*
  as separate intents, and the recovery posture: never resume the cut sentence,
  the user's content leads, one clause at most. Maps each pattern to its
  library seam (`should_speak`, the async turn lock, the `is_busy` scheduler
  gate, the `interrupted` canned line).
- `docs/STYLE_SPECTRUM.md` + `examples/style_spectrum.py`: the style spectrum —
  an optional app-side renderer turning affect bands into at most three
  form-only "delivery notes" (punctuation temperature, sentence length, doubled
  words, filler budget). Addresses the description-vs-instruction tension head
  on: form never content, off-neutral only, examples inline, hard cap; a
  suppressed release owns the whole delivery. Deterministic runnable demo.
- `docs/AGENT_WORK_UX.md` + `examples/agent_narration.py`: working out loud —
  the canned-voicebank pattern for long agent work (pools per event kind,
  thinking-pool for memory digs, ok/fail/empty result pools, honest progress
  counts, distinct completion cues), the narration throttle ("the mouth rests,
  the hands don't"), and carrying finished work across turns through the
  proactive path. Deterministic runnable demo; its transcript is the doc's.
- `docs/FAILURE_IN_CHARACTER.md`: errors that don't break the fourth wall —
  two audiences two truths (user hears a person, operator log keeps the
  exception), felt failure kinds over exception classes (net_down /
  model_stalled / interrupted) with a cheap-probe diagnosis, delivery rules,
  the watchdog case (the system speaks *as her* when she can't), and recovery
  etiquette.
- `docs/PROMPT_STACK.md`: the prompt-stack chapter — the static/dynamic
  partition with the reason each line sits where it does, the sandwich
  structure and the ordering physics of the bottom slice, and the
  **forget-probe pattern**: a per-turn checkable micro-act (the sentence-initial
  emotion tag) whose absence triggers a one-turn identity reminder —
  persona upkeep that costs nothing until it's needed.
- `docs/OUTPUT_CHAIN.md`: the output-chain chapter — the two signal channels
  (state-edge → face, reply-tag → voice colour) with the live transcript
  showing them deliberately disagreeing, first-sentence TTS streaming,
  interruption etiquette via `should_speak`, and renderer portability down to
  a global-hotkey bridge (the label is the protocol).
- `examples/prompt_shapes.py` + `docs/PROMPT_SHAPES.md`: the prompt-shape
  gallery — one neutral persona, three prepared moments (first morning, back
  after three days, a release let out vs held in), each shown as the full
  message array; plus the variant master table mapping every backend signal to
  its phrase bands and thresholds ("state moves → one line moves").
- `examples/memory_tools.py` + `docs/MEMORY_TOOLS.md`: Canon exposed as five
  function-calling tools (remember / recall / correct / retract / history) with
  copy-paste JSON schemas, the ~30-line dispatcher, and a real four-turn trace
  ending in the bi-temporal "what did I *used to* think?" answer.
- `examples/companion_live.py`: an *interactive* companion loop — you type, a
  real background heartbeat runs, going quiet makes it raise a pending topic on
  its own, and facts stored in `Canon` survive a full process restart. Zero
  network/API; fast-timer mode via `FELTSTATE_LIVE_FAST=1`.
- `docs/INTEGRATION.md`: the assembly manual — wiring diagram (foreground turn,
  heartbeat thread, memory tools), the prompt partition table and its caching
  economics, heartbeat cadence knobs, the propose/dispatch/commit proactive
  path, adapter swap table, and the privacy boundary for chat-platform bridges.
  Every quoted transcript is real output of the live example.
- Regression suite `tests/test_bugfix_20260718.py` pinning each fix above
  (frequency-invariant imprint decay, NaN rejection, mixed-frame tolerance,
  settle direction, joy-band exclusion, fcntl-free locking, concurrent
  add/recall integrity, archive-first compaction ordering).

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

[Unreleased]: https://github.com/Morephine/feltstate/compare/v0.2.0a2...HEAD
[0.2.0a2]: https://github.com/Morephine/feltstate/releases/tag/v0.2.0a2
[0.2.0a1]: https://github.com/Morephine/feltstate/compare/v0.1.0...v0.2.0a1
[0.1.0]: https://github.com/Morephine/feltstate/releases/tag/v0.1.0
