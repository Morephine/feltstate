"""feltstate.affect.pressure — the multi-bar pressure cooker and its dynamics.

Emotion is not one scalar. Sadness, anger, anxiety, boundary-violation and joy
fill up in **separate reservoirs**; whichever crosses threshold first is what
gets released. This is the dynamics layer for the :class:`~feltstate.state.PressureState`
schema — the data shape lives in :mod:`feltstate.state`, every tunable in
:class:`~feltstate.config.PressureConfig`, and nothing character-specific is
hard-coded here (personality enters only through :class:`~feltstate.config.PersonaDials`).

The model is a four-phase loop::

    calm        (all bars below build-up threshold)
      | accumulate
    building    (a bar climbs past the build-up threshold)
      | a bar crosses the release threshold
    releasing   (one to two turns of strong expression)
      | the release window elapses
    aftertaste  (the feeling lingers, per-type duration)
      | the aftertaste window elapses
    calm        (bars settle to a floor — not zero)

Two refinements over a naive accumulator:

* **Hybrid release.** If two bars cross together and their weighted scores are
  within :attr:`~feltstate.config.PressureConfig.threshold_hybrid`, the release
  is a blend (a primary flavour with a secondary one — e.g. anger shot through
  with tears).
* **Collapse.** If :attr:`~feltstate.config.PressureConfig.threshold_collapse`
  or more bars are high at once, the system floods: an incoherent release rather
  than one clean channel.

And two that make it feel human rather than mechanical:

* **Power-aware expression.** A Lazarus/Bandura *power* appraisal (perceived
  control / self-efficacy, computed from traits and relationship) decides not
  *whether* to release but *how*: high power expresses openly (``tears``,
  ``anger`` ...), low power suppresses (``tears_suppress`` ...). The threshold to
  release is the same; only the channel differs.
* **Valence-opposite mutual inhibition.** You do not laugh while crying. When
  the sadness bar takes inflow it drains the joy bar (and vice versa, joy also
  lightly damping anger). Same-cluster bars (anger/anxiety, both high-arousal
  negative) do not inhibit each other.

Decay back to neutral is built in: every tick applies
:attr:`~feltstate.config.PressureConfig.idle_decay`, floored by a trait-derived
residual (a chronically low temperament keeps a little sadness in the tank even
at rest). Accumulation is suspended while ``releasing`` or in ``aftertaste`` —
humans do not re-stack pressure while still venting it.

The text/voice of a release (what words the character actually uses) is a
product concern and lives nowhere in this module. Here we only move numbers and
phases.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..config import (
    BAR_TO_RELEASE,
    BAR_TO_RELEASE_SUPPRESS,
    LABEL_TO_PRESSURE,
    PersonaDials,
    PressureConfig,
)
from ..state import (
    BAR_NAMES,
    AffectDelta,
    PressureState,
    Relationship,
    Traits,
)

__all__ = ["step", "compute_power"]


# --------------------------------------------------------------------------- #
# Small internal derivation factors (NOT user tunables; they are the shape of  #
# a formula, not a knob). Everything a caller should ever touch is in          #
# PressureConfig. These two only translate a [0,1] level into a slope.         #
# --------------------------------------------------------------------------- #
# How steeply a trait above 0.5 raises the resting floor / drives baseline
# inflow for its matching bar: e.g. depression 0.83 -> (0.83-0.5)*0.4 ~= 0.13.
_TRAIT_SLOPE = 0.4
# Above this trait level, the temperament starts feeding its bar every tick
# (a chronically anxious agent simmers even with neutral input).
_TRAIT_FEED_ABOVE = 0.70


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Power — Lazarus appraisal of perceived control / self-efficacy.             #
# --------------------------------------------------------------------------- #
def compute_power(traits: Traits, relationship: Relationship, cfg: PressureConfig) -> float:
    """Return the agent's *power* in ``[0, 1]`` — its felt control / self-efficacy.

    High power means it feels safe and capable enough to express what it feels
    openly; low power means it suppresses. This is the Lazarus appraisal of
    coping potential (with a nod to Bandura's self-efficacy and Rotter's locus of
    control): the same released emotion comes out as ``anger`` when power is high
    and ``anger_suppress`` when it is low.

    The blend is driven entirely by :attr:`PressureConfig.power_weights`, whose
    keys name either a trait, a ``*_inv`` trait (contributes ``1 - value``), or a
    relationship field. Unknown keys are ignored, so the weight set can be
    re-tuned in config without touching this function.
    """
    weights = cfg.power_weights or {}
    power = 0.0
    for key, w in weights.items():
        if key.endswith("_inv"):
            base = key[: -len("_inv")]
            val = _trait_or_rel(base, traits, relationship)
            power += (1.0 - val) * w
        else:
            val = _trait_or_rel(key, traits, relationship)
            power += val * w
    return _clamp01(power)


def _trait_or_rel(name: str, traits: Traits, relationship: Relationship) -> float:
    """Look ``name`` up as a trait first, then a relationship field; 0.5 default."""
    if hasattr(traits, name):
        return _clamp01(float(getattr(traits, name)))
    if hasattr(relationship, name):
        return _clamp01(float(getattr(relationship, name)))
    return 0.5


# --------------------------------------------------------------------------- #
# Phase 1 — accumulation                                                      #
# --------------------------------------------------------------------------- #
def _anticipation_progress(ant: dict, ts: str) -> float:
    """How far a scheduled anticipation has come, in ``[0, 1]``.

    If ``ant`` carries an ``event_ts`` (when the looked-forward-to thing happens)
    and a ``since_ts`` (when it was first registered), this ramps linearly from
    registration toward the event — excitement building as it nears. With no
    schedule it returns ``1.0``: a flat, always-on anticipation floor.
    """
    event = ant.get("event_ts")
    if not event:
        return 1.0
    try:
        ev = _parse(str(event))
        now = _parse(ts)
        since = ant.get("since_ts")
        start = _parse(str(since)) if since else now
        total = (ev - start).total_seconds()
        if total <= 0:
            return 1.0
        elapsed = (now - start).total_seconds()
        return max(0.0, min(1.0, elapsed / total))
    except (ValueError, TypeError):
        return 1.0


def _accumulate(
    pressure: PressureState,
    *,
    delta: AffectDelta,
    traits: Traits,
    relationship: Relationship,
    cfg: PressureConfig,
    ts: str,
) -> None:
    """Add this turn's inflow into the bars (mutating ``pressure.bars``).

    Inflow comes from three places, merged so nothing double-counts:

    1. the measured emotion *labels* of the turn, routed through
       :data:`~feltstate.config.LABEL_TO_PRESSURE` (max per bar);
    2. a slow trait-driven simmer (a high-depression / high-anxiety temperament
       feeds its bar a little every tick) plus relationship tension feeding anger
       and boundary;
    3. discrete *milestone* shocks (appraised events such as care, conflict,
       loss) — one-off impulses with a sign and a severity.

    Finally, valence-opposite mutual inhibition drains the antagonist bar.
    Called only when the cooker is not already releasing/aftertaste.
    """
    inflow = {k: 0.0 for k in BAR_NAMES}

    # --- (1) label-driven inflow, max() per bar so duplicates don't stack ---
    labels = list(delta.labels or [])
    for label in labels:
        for bar, amount in (LABEL_TO_PRESSURE.get(label) or {}).items():
            if bar in inflow:
                inflow[bar] = max(inflow[bar], float(amount))

    # --- (2) trait / relationship simmer ---
    depression = _clamp01(float(traits.depression))
    anxiety_t = _clamp01(float(traits.anxiety))
    tension = max(0.0, float(relationship.unresolved_tension))

    if depression > _TRAIT_FEED_ABOVE:
        inflow["sadness"] += 0.015 * (depression - _TRAIT_FEED_ABOVE) / (1.0 - _TRAIT_FEED_ABOVE)
    if anxiety_t > _TRAIT_FEED_ABOVE:
        inflow["anxiety"] += 0.013 * (anxiety_t - _TRAIT_FEED_ABOVE) / (1.0 - _TRAIT_FEED_ABOVE)

    # Standing friction with the person leaks into anger, and (when it runs high)
    # into the boundary bar — the urge to withdraw or draw a line.
    if tension > 0.5:
        inflow["anger"] += 0.013
    if tension > 0.6:
        inflow["boundary"] += 0.013

    # A negative-valence turn nudges sadness; a positive one nudges joy. This is
    # the affective pull of the reading itself, on top of any labels.
    v = float(delta.valence)
    if v < -0.3:
        inflow["sadness"] += abs(v) * 0.02
    elif v > 0.2:
        inflow["joy"] += v * 0.02

    # An anticipated good thing keeps a little joy in the tank, proportional to how
    # much it is looked forward to (weight x positive valence). If the anticipation
    # carries a schedule (``event_ts`` + ``since_ts``), the floor ramps from
    # registration up toward the event — excitement building as it nears. Optional.
    ant = delta.anticipation
    if isinstance(ant, dict):
        a_v = float(ant.get("valence", 0.0))
        a_w = float(ant.get("weight", 0.0))
        if a_v > 0 and a_w > 0:
            progress = _anticipation_progress(ant, ts)
            inflow["joy"] = max(inflow["joy"], 0.5 * a_v * a_w * progress * cfg.idle_decay / 0.018)

    # --- (3) milestone shocks ---
    for m in delta.milestones or []:
        _apply_milestone(inflow, pressure, m)

    # --- valence-opposite mutual inhibition (you don't laugh while crying) ---
    sad_in = inflow["sadness"]
    joy_in = inflow["joy"]
    inh = float(cfg.inhibition)
    if sad_in > 0.005:
        pressure.bars.joy = max(0.0, pressure.bars.joy - sad_in * inh)
    if joy_in > 0.005:
        pressure.bars.sadness = max(0.0, pressure.bars.sadness - joy_in * inh)
        # joy also lightly damps anger — it is hard to stay furious while elated
        pressure.bars.anger = max(0.0, pressure.bars.anger - joy_in * inh * 0.5)

    # commit inflow
    for k in BAR_NAMES:
        setattr(pressure.bars, k, getattr(pressure.bars, k) + inflow[k])


def _apply_milestone(inflow: dict, pressure: PressureState, m: dict) -> None:
    """Route one appraised event onto the bars.

    ``kind`` selects the channel; ``severity`` (default 0.5) scales the deeper
    shocks; ``actor`` distinguishes something the *user* did from something the
    agent did. Care/repair events also *dampen* whatever negative inflow this
    turn already had — being comforted blunts the sadness, not just adds joy.
    """
    kind = str(m.get("kind", ""))
    actor = m.get("actor")
    sev = float(m.get("severity", 0.5))

    if kind == "conflict":
        inflow["anger"] += 0.025
        inflow["sadness"] += 0.015
    elif kind in ("rejection", "rejection_or_boundary", "boundary"):
        inflow["sadness"] += 0.02
        inflow["boundary"] += 0.02
    elif kind in ("confession", "confession_emotional", "confession_romantic") and actor == "user":
        inflow["joy"] += 0.04
    elif kind == "repair":
        inflow["sadness"] *= 0.7  # making up shrinks the sadness that built this turn
        inflow["anger"] *= 0.6
    elif kind == "care" and actor == "user":
        inflow["joy"] += 0.04
        inflow["sadness"] *= 0.6
        inflow["anger"] *= 0.7
    # Warmth family — positive deep imprints, scaled by severity.
    elif kind in ("warmth_love", "love"):
        inflow["joy"] += 0.20 * sev
        inflow["sadness"] *= 0.7
    elif kind in ("warmth_gratitude", "gratitude"):
        inflow["joy"] += 0.15 * sev
    elif kind in ("warmth_secure", "reassurance"):
        inflow["joy"] += 0.12 * sev
        inflow["anxiety"] *= 0.7
    # Trauma family — negative deep imprints, scaled by severity.
    elif kind in ("trauma_betrayal", "betrayal"):
        inflow["sadness"] += 0.30 * sev
        inflow["anger"] += 0.25 * sev
        inflow["boundary"] += 0.20 * sev
        inflow["joy"] = max(0.0, inflow["joy"] - 0.15 * sev)
    elif kind in ("trauma_loss", "loss"):
        inflow["sadness"] += 0.40 * sev
        inflow["joy"] = max(0.0, inflow["joy"] - 0.20 * sev)
    elif kind in ("trauma_disappointment", "disappointment"):
        inflow["sadness"] += 0.25 * sev
        inflow["anger"] += 0.10 * sev
        inflow["joy"] = max(0.0, inflow["joy"] - 0.15 * sev)


# --------------------------------------------------------------------------- #
# Decay + trait floor (always applied, even mid-release)                      #
# --------------------------------------------------------------------------- #
def _decay_and_floor(pressure: PressureState, traits: Traits, cfg: PressureConfig) -> None:
    """Apply natural cooling to every bar and clamp to a trait-derived floor.

    This runs every tick regardless of phase — feelings cool whether or not the
    agent is mid-release. The floor is what keeps decay from erasing a chronic
    temperament: a high-depression agent's sadness bar never falls all the way to
    zero, a high-optimism agent keeps a little joy on tap.
    """
    decay = float(cfg.idle_decay)
    floors = {
        "sadness": max(0.0, (float(traits.depression) - 0.5) * _TRAIT_SLOPE),
        "anxiety": max(0.0, (float(traits.anxiety) - 0.5) * _TRAIT_SLOPE),
        "joy": max(0.0, (float(traits.optimism) - 0.5) * _TRAIT_SLOPE),
    }
    for k in BAR_NAMES:
        cur = getattr(pressure.bars, k) - decay
        cur = max(cur, floors.get(k, 0.0))
        setattr(pressure.bars, k, _clamp01(cur))


# --------------------------------------------------------------------------- #
# Phase 2 — release selection (power-aware, hybrid, collapse)                 #
# --------------------------------------------------------------------------- #
def _release_weight(release_type: str, dials: PersonaDials) -> float:
    """Personality preference for a release channel (``>1`` = preferred).

    These biases shape *which* channel wins a tie between two crossed bars; they
    never change whether a release fires. Only the open (expressive) channels
    carry a preference — the suppressed counterparts fall back to neutral weight,
    since suppression is itself the low-power default.
    """
    w = float(dials.warmth)
    restraint = float(dials.restraint)
    vuln = float(dials.vulnerability)
    direct = float(dials.directness)
    bnd = float(dials.boundary_strength)
    eexp = float(dials.emotional_explicitness)

    if release_type == "tears":
        return 1.0 + (vuln - 0.5) * 0.6 - (restraint - 0.5) * 0.5
    if release_type == "anger":
        return 1.0 + (direct - 0.5) * 0.5 - (w - 0.5) * 0.4
    if release_type == "anxious":
        return 1.0 + (eexp - 0.5) * 0.4
    if release_type == "withdraw":
        return 1.0 + (bnd - 0.5) * 0.7 + (restraint - 0.5) * 0.3
    if release_type == "burst_joy":
        return 1.0 + (eexp - 0.5) * 0.5
    return 1.0


def _select_release(
    pressure: PressureState,
    *,
    dials: PersonaDials,
    cfg: PressureConfig,
    power: float,
    ts: str,
) -> dict | None:
    """Decide the release if any bar is at/above the release threshold, else ``None``.

    Returns a decision dict carrying the primary (and, for a hybrid, secondary)
    channel, the collapse flag, the durations, and the power band. The channel
    map is chosen by ``power`` against
    :attr:`~feltstate.config.PressureConfig.power_threshold`: above it the open
    map (:data:`~feltstate.config.BAR_TO_RELEASE`), at or below it the suppressed
    map (:data:`~feltstate.config.BAR_TO_RELEASE_SUPPRESS`).
    """
    crossed = pressure.bars.at_or_above(cfg.threshold_release)
    if not crossed:
        return None

    if power > cfg.power_threshold:
        bar_map = BAR_TO_RELEASE
        power_band = "express"
    else:
        bar_map = BAR_TO_RELEASE_SUPPRESS
        power_band = "suppress"

    started = ts
    started_dt = _parse(ts)

    # Too many bars high at once -> emotional flooding (collapse).
    if len(crossed) >= cfg.threshold_collapse:
        _lo, hi = cfg.release_duration_min.get("collapse", (10, 20))
        return {
            "primary_bar": crossed[0][0],
            "primary_release": "collapse",
            "secondary_bar": None,
            "secondary_release": None,
            "is_hybrid": False,
            "is_collapse": True,
            "power": power,
            "power_band": power_band,
            "started_ts": started,
            "ends_ts": (started_dt + timedelta(minutes=hi)).isoformat(),
            "all_bars_high": [c[0] for c in crossed],
        }

    # One or two crossed: weight by personality preference, pick the winner.
    weighted = []
    for bar_name, bar_val in crossed:
        rel_type = bar_map[bar_name]
        # Preference is only defined for the open channels; suppressed channels
        # (and unknown ones) get neutral weight.
        w = 1.0 if rel_type.endswith("_suppress") else _release_weight(rel_type, dials)
        weighted.append((bar_name, bar_val, rel_type, bar_val * w))
    weighted.sort(key=lambda x: x[3], reverse=True)
    primary_bar, _, primary_release, primary_score = weighted[0]

    secondary_bar = secondary_release = None
    is_hybrid = False
    if len(weighted) > 1:
        sec_bar, _, sec_release, sec_score = weighted[1]
        if (primary_score - sec_score) < cfg.threshold_hybrid:
            secondary_bar, secondary_release, is_hybrid = sec_bar, sec_release, True

    # Duration table is keyed by the open channel name; suppressed reuses it.
    dur_key = primary_release.replace("_suppress", "")
    _lo, hi = cfg.release_duration_min.get(dur_key, (5, 15))
    return {
        "primary_bar": primary_bar,
        "primary_release": primary_release,
        "secondary_bar": secondary_bar,
        "secondary_release": secondary_release,
        "is_hybrid": is_hybrid,
        "is_collapse": False,
        "power": power,
        "power_band": power_band,
        "started_ts": started,
        "ends_ts": (started_dt + timedelta(minutes=hi)).isoformat(),
    }


def _trigger_release(pressure: PressureState, decision: dict, cfg: PressureConfig) -> None:
    """Move the cooker into ``releasing`` from a :func:`_select_release` decision.

    Sets the release channel(s) and the timing windows (when this release ends,
    and when the trailing aftertaste ends), and appends a compact record to
    ``pressure.history`` (last five releases retained).
    """
    pressure.phase = "releasing"
    pressure.release_type = decision["primary_release"]
    pressure.release_secondary = decision.get("secondary_release")
    pressure.release_started_ts = decision["started_ts"]
    pressure.release_ends_ts = decision["ends_ts"]

    dur_key = str(decision["primary_release"]).replace("_suppress", "")
    aftertaste_min = cfg.aftertaste_duration_min.get(dur_key, 30)
    pressure.aftertaste_until_ts = (
        _parse(decision["ends_ts"]) + timedelta(minutes=aftertaste_min)
    ).isoformat()

    pressure.history.append(
        {
            "ts": decision["started_ts"],
            "release_type": decision["primary_release"],
            "secondary": decision.get("secondary_release"),
            "is_collapse": decision.get("is_collapse", False),
            "trigger_bars": {k: round(getattr(pressure.bars, k), 3) for k in BAR_NAMES},
        }
    )
    pressure.history = pressure.history[-5:]


# --------------------------------------------------------------------------- #
# Phase 3 — time-based phase progression                                      #
# --------------------------------------------------------------------------- #
def _advance_phase(pressure: PressureState, cfg: PressureConfig, ts: str) -> None:
    """Walk the phase machine forward by the clock and by bar levels.

    Time-driven: ``releasing`` -> ``aftertaste`` once the release window passes,
    then ``aftertaste`` -> ``calm`` once the aftertaste window passes (at which
    point bars are pulled most of the way down to the floor — a release should
    *feel* like relief, not a 30%% trim). Level-driven, with hysteresis: ``calm``
    rises to ``building`` above the build-up threshold, and ``building`` falls
    back to ``calm`` below the (lower) build-down threshold. The two thresholds
    are separated so a bar hovering near the line does not flicker phases.
    """
    now = _parse(ts)

    # releasing -> aftertaste
    if pressure.phase == "releasing" and pressure.release_ends_ts:
        if now >= _parse(pressure.release_ends_ts):
            pressure.phase = "aftertaste"
            if not pressure.aftertaste_until_ts:
                # Defensive: never let a missing field deadlock the cooker.
                pressure.aftertaste_until_ts = (now + timedelta(minutes=30)).isoformat()

    # aftertaste with no end set (stale state) -> escape to calm
    if pressure.phase == "aftertaste" and pressure.aftertaste_until_ts is None:
        _reset_to_calm(pressure)

    # aftertaste -> calm, settling bars toward the floor
    if pressure.phase == "aftertaste" and pressure.aftertaste_until_ts:
        if now >= _parse(pressure.aftertaste_until_ts):
            _reset_to_calm(pressure)
            floor = float(cfg.bar_floor)
            keep = float(cfg.reset_keep)
            for k in BAR_NAMES:
                cur = getattr(pressure.bars, k)
                setattr(pressure.bars, k, floor + (cur - floor) * keep)

    # calm <-> building by level, with hysteresis (single transition per tick)
    _max_name, max_val = pressure.bars.max_bar()
    if pressure.phase == "calm":
        if max_val > cfg.threshold_build_up:
            pressure.phase = "building"
    elif pressure.phase == "building":
        if max_val < cfg.threshold_build_down:
            pressure.phase = "calm"


def _reset_to_calm(pressure: PressureState) -> None:
    """Clear all release bookkeeping and return the phase to ``calm``."""
    pressure.phase = "calm"
    pressure.release_type = None
    pressure.release_secondary = None
    pressure.release_started_ts = None
    pressure.release_ends_ts = None
    pressure.aftertaste_until_ts = None


def _parse(ts: str) -> datetime:
    """Parse an ISO timestamp; treat naive timestamps as UTC for stable arithmetic."""
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# --------------------------------------------------------------------------- #
# The public tick                                                             #
# --------------------------------------------------------------------------- #
def step(
    pressure: PressureState,
    *,
    delta: AffectDelta,
    traits: Traits,
    relationship: Relationship,
    dials: PersonaDials,
    cfg: PressureConfig,
    ts: str,
) -> PressureState:
    """Advance the pressure cooker by one full tick and return it.

    A tick runs in order:

    1. **accumulate** this turn's reading into the bars — but only when the
       cooker is ``calm``/``building`` (a ``releasing``/``aftertaste`` cooker
       suspends inflow, so the agent does not re-stack pressure while venting);
    2. **cool** every bar by :attr:`~feltstate.config.PressureConfig.idle_decay`,
       clamped to a trait-derived floor (always applied, every phase);
    3. **select a release** if any bar crossed
       :attr:`~feltstate.config.PressureConfig.threshold_release` (power-aware
       channel, with hybrid/collapse handling) and, if so, move into
       ``releasing``;
    4. **advance the phase machine** by the clock and by bar levels.

    The passed ``pressure`` is mutated in place and also returned for
    convenience. ``ts`` is the tick's ISO timestamp (its caller's clock); all
    release/aftertaste windows are computed from it, so feeding a monotonic clock
    keeps the dynamics deterministic and testable.
    """
    ts = ts or _now_iso()

    # (1) accumulate — only outside the vent.
    if pressure.phase not in ("releasing", "aftertaste"):
        _accumulate(
            pressure,
            delta=delta,
            traits=traits,
            relationship=relationship,
            cfg=cfg,
            ts=ts,
        )

    # (2) decay + trait floor, every tick.
    _decay_and_floor(pressure, traits, cfg)

    # (3) release selection — only when not already venting.
    if pressure.phase not in ("releasing", "aftertaste"):
        power = compute_power(traits, relationship, cfg)
        decision = _select_release(pressure, dials=dials, cfg=cfg, power=power, ts=ts)
        if decision is not None:
            _trigger_release(pressure, decision, cfg)

    # (4) phase progression by clock + levels.
    _advance_phase(pressure, cfg, ts)

    pressure.last_tick_ts = ts
    return pressure
