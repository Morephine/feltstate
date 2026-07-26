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

import math
from datetime import datetime, timedelta, timezone

from ..config import (
    BAR_TO_RELEASE,
    BAR_TO_RELEASE_SUPPRESS,
    LABEL_TO_PRESSURE,
    MILESTONE_ALIASES,
    MILESTONE_EFFECTS,
    PersonaDials,
    PressureConfig,
)
from ..state import (
    BAR_NAMES,
    AffectDelta,
    PressureState,
    Relationship,
    Traits,
    _finite,
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
    ticks: float = 1.0,
) -> None:
    """Add this turn's inflow into the bars (mutating ``pressure.bars``).

    Inflow comes from three places, merged so nothing double-counts:

    1. the estimated emotion *labels* of the turn, routed through
       :data:`~feltstate.config.LABEL_TO_PRESSURE` (max per bar);
    2. a slow trait-driven simmer (a high-depression / high-anxiety temperament
       feeds its bar a little *per unit time*) plus relationship tension feeding
       anger and boundary;
    3. discrete *milestone* shocks (appraised events such as care, conflict,
       loss) — one-off impulses with a sign and a severity.

    ``ticks`` is the elapsed wall-clock span since the previous tick in reference
    ticks (one tick = one minute). Only the **continuous** trait/tension simmer
    (item 2) scales with it — it is a background drip that accrues per unit time,
    so a bar must not simmer five times faster merely because it is ticked five
    times as often. The label, valence and milestone inflows (items 1 and 3) are
    *per-event* readings of this turn and are deliberately not elapsed-scaled.

    Finally, valence-opposite mutual inhibition drains the antagonist bar.
    Called only when the cooker is not already releasing/aftertaste.
    """
    inflow = {k: 0.0 for k in BAR_NAMES}

    # --- (1) label-driven inflow, max() per bar so duplicates don't stack ---
    # label_pressure_scale rescales this channel for fast tick rates (agent
    # steps vs conversation turns); milestone/trait/valence inflow is untouched.
    labels = list(delta.labels or [])
    scale = float(getattr(cfg, "label_pressure_scale", 1.0))
    for label in labels:
        for bar, amount in (LABEL_TO_PRESSURE.get(label) or {}).items():
            if bar in inflow:
                inflow[bar] = max(inflow[bar], float(amount) * scale)

    # --- (2) trait / relationship simmer (continuous; scales with elapsed time) ---
    depression = _clamp01(float(traits.depression))
    anxiety_t = _clamp01(float(traits.anxiety))
    tension = max(0.0, float(relationship.unresolved_tension))

    if depression > _TRAIT_FEED_ABOVE:
        inflow["sadness"] += (
            0.015 * (depression - _TRAIT_FEED_ABOVE) / (1.0 - _TRAIT_FEED_ABOVE) * ticks
        )
    if anxiety_t > _TRAIT_FEED_ABOVE:
        inflow["anxiety"] += (
            0.013 * (anxiety_t - _TRAIT_FEED_ABOVE) / (1.0 - _TRAIT_FEED_ABOVE) * ticks
        )

    # Standing friction with the person leaks into anger, and (when it runs high)
    # into the boundary bar — the urge to withdraw or draw a line.
    if tension > 0.5:
        inflow["anger"] += 0.013 * ticks
    if tension > 0.6:
        inflow["boundary"] += 0.013 * ticks

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
    joy_floor = 0.0
    if isinstance(ant, dict):
        a_v = _finite(ant.get("valence", 0.0), 0.0)
        a_w = _finite(ant.get("weight", 0.0), 0.0)
        if a_v > 0 and a_w > 0:
            progress = _anticipation_progress(ant, ts)
            # Floor scaled by idle_decay / ref so it outlasts the per-tick
            # cooling it competes with; both knobs are named in PressureConfig.
            joy_floor = (
                cfg.anticipation_gain
                * a_v
                * a_w
                * progress
                * cfg.idle_decay
                / cfg.anticipation_ref_decay
            )

    # --- (3) milestone shocks ---
    for m in delta.milestones or []:
        _apply_milestone(inflow, m)

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

    # Plasticity: hits are metered on the RAW merged inflow (pre-gain — the
    # stimulus, not the amplified echo), then the commit below multiplies by
    # the carved gain. Merging already guarantees one hit per bar per tick.
    if cfg.plasticity:
        _plast_register_hits(pressure, inflow, cfg)

    # commit inflow
    for k in BAR_NAMES:
        gain = _plast_gain(pressure, k, cfg) if cfg.plasticity else 1.0
        setattr(pressure.bars, k, getattr(pressure.bars, k) + inflow[k] * gain)

    # An anticipated good thing keeps a little joy in the tank — a FLOOR under
    # the bar, applied after the commit, not an inflow added to it.
    #
    # It used to be max()-ed into inflow["joy"], which meant it was added again
    # on every tick: a standing anticipation ratcheted the bar monotonically up
    # to the release threshold and fired burst_joy out of nothing (measured:
    # calm -> building in five minutes, releasing in six). It was also the only
    # continuous inflow not scaled by `ticks`, so the climb depended on how
    # often step() happened to be called — the same anticipation reached
    # releasing at a 1-minute cadence and stayed at zero at 5 minutes. And via
    # mutual inhibition below it silently drained sadness and anger every tick.
    #
    # As a floor it does what the docstring says: while the anticipation is
    # live the joy bar cannot fall below it, and it cannot push it any higher.
    if joy_floor > 0.0:
        pressure.bars.joy = max(pressure.bars.joy, min(1.0, joy_floor))


# --------------------------------------------------------------------------- #
# Plasticity — what fires, sensitizes; what is safe, heals                    #
# --------------------------------------------------------------------------- #
def _sens_of(pressure: PressureState, bar: str, cfg: PressureConfig) -> float:
    """Read one bar's sensitivity; missing/non-finite values read as 0.5."""
    try:
        v = float(pressure.sensitivity.get(bar, 0.5))
    except (TypeError, ValueError, AttributeError):
        return 0.5
    if not math.isfinite(v):
        return 0.5
    return max(cfg.plast_floor, min(cfg.plast_ceil, v))


def _plast_register_hits(pressure: PressureState, inflow: dict, cfg: PressureConfig) -> None:
    """Meter this tick's raw inflow into micro sensitivity hits.

    Two charge grades (ordinary label traffic vs event-grade shocks), each
    adding a *micro* increment — single digits of 1e-6 — so character change
    is an accumulation of lived days, never one loud message. Rounded to 8
    decimals: at micro scale a coarser rounding would quantize small
    updates to zero and the sensitivity could never move (or come home).
    """
    for k in BAR_NAMES:
        d = inflow.get(k, 0.0)
        try:
            d = float(d)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(d):
            continue
        if d >= cfg.plast_charge_heavy:
            inc = cfg.plast_hit_heavy
        elif d >= cfg.plast_charge_light:
            inc = cfg.plast_hit_light
        else:
            continue
        pressure.sensitivity[k] = round(min(cfg.plast_ceil, _sens_of(pressure, k, cfg) + inc), 8)


def _plast_gain(pressure: PressureState, bar: str, cfg: PressureConfig) -> float:
    """Inflow multiplier from carved sensitivity: 1 + k×(sens − 0.5)."""
    return 1.0 + cfg.plast_gain_k * (_sens_of(pressure, bar, cfg) - 0.5)


def _plast_heal(
    pressure: PressureState, relationship: Relationship, cfg: PressureConfig, ts: str
) -> None:
    """Percentage healing of sensitivity toward the 0.5 baseline.

    The daily rate interpolates between ``plast_decay_lo`` (safety 0) and
    ``plast_decay_hi`` (safety 1): a safe bond softens carved edges faster.
    Settled over *real elapsed days* since the anchor, and the anchor is
    stamped **before** applying — advance-always, so healing is
    frequency-invariant and a missed stamp can never recharge the whole
    window twice (the imprint quadratic-decay lesson, applied here from
    birth).
    """
    try:
        now_dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        now_dt = datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    prev = pressure.sens_last_decay_ts
    pressure.sens_last_decay_ts = now_dt.isoformat()
    if not prev:
        return  # first sighting just plants the anchor
    try:
        prev_dt = datetime.fromisoformat(str(prev).replace("Z", "+00:00"))
        if prev_dt.tzinfo is None:
            prev_dt = prev_dt.replace(tzinfo=timezone.utc)
        days = (now_dt - prev_dt).total_seconds() / 86400.0
    except (ValueError, TypeError):
        return
    if days <= 0:
        return
    safety = relationship.safety
    try:
        safety = float(safety)
    except (TypeError, ValueError):
        safety = 0.5
    if not math.isfinite(safety):
        safety = 0.5
    safety = max(0.0, min(1.0, safety))
    pct = cfg.plast_decay_lo + (cfg.plast_decay_hi - cfg.plast_decay_lo) * safety
    keep = (1.0 - pct) ** days
    for k in BAR_NAMES:
        cur = _sens_of(pressure, k, cfg)
        # Rounded to 12 places, not 8. The anchor is stamped advance-always, so
        # whatever a pass rounds away is not deferred — it is discarded. At a
        # one-minute cadence a pass heals dev * ~3.5e-6, which for a lightly
        # carved bar (dev ~1e-3) is ~3.5e-9: entirely below 8 places. A week of
        # healing then came to exactly zero at one-minute ticks while the same
        # week at hourly ticks healed 3.4e-5 — a one-way ratchet for any
        # character ticked often, which is precisely the busy one. 12 places is
        # still far coarser than float noise and keeps the file readable.
        pressure.sensitivity[k] = round(0.5 + (cur - 0.5) * keep, 12)


def _apply_milestone(inflow: dict, m: dict) -> None:
    """Route one appraised event onto the bars.

    ``kind`` selects the channel; ``severity`` (default 0.5) scales the deeper
    shocks; ``actor`` distinguishes something the *user* did from something the
    agent did. Care/repair events also *dampen* whatever negative inflow this
    turn already had — being comforted blunts the sadness, not just adds joy.

    The numbers live in :data:`feltstate.config.MILESTONE_EFFECTS` (with kind
    synonyms in :data:`~feltstate.config.MILESTONE_ALIASES`) — a tuning table,
    kept in config like every other one. This function only interprets it.
    """
    kind = str(m.get("kind", ""))
    actor = m.get("actor")
    # Unsanitised milestone field — see relationship.py. NaN would clamp to the
    # maximum and dump a full-severity shock into the bars.
    sev = _finite(m.get("severity", 0.5), 0.5)

    effect = MILESTONE_EFFECTS.get(MILESTONE_ALIASES.get(kind, kind))
    if effect is None:
        return
    required = effect.get("require_actor")
    if required is not None and actor != required:
        return

    for bar, inc in effect.get("add", {}).items():
        inflow[bar] += inc
    for bar, inc in effect.get("add_sev", {}).items():
        inflow[bar] += inc * sev
    for bar, dec in effect.get("sub_sev", {}).items():
        inflow[bar] = max(0.0, inflow[bar] - dec * sev)
    for bar, factor in effect.get("scale", {}).items():
        inflow[bar] *= factor


# --------------------------------------------------------------------------- #
# Decay + trait floor (always applied, even mid-release)                      #
# --------------------------------------------------------------------------- #
def _decay_and_floor(
    pressure: PressureState, traits: Traits, cfg: PressureConfig, ticks: float = 1.0
) -> None:
    """Cool every bar by elapsed time and clamp to a trait-derived floor.

    This runs every tick regardless of phase — feelings cool whether or not the
    agent is mid-release. The floor is what keeps decay from erasing a chronic
    temperament: a high-depression agent's sadness bar never falls all the way to
    zero, a high-optimism agent keeps a little joy on tap.

    ``ticks`` is the elapsed span since the previous tick in reference ticks (one
    tick = one minute). The cooling is ``idle_decay`` **per reference tick**, so
    the total cooling over a fixed real interval is the same however finely the
    interval is ticked — the bar cooldown is a function of wall-clock time, not of
    call count. This linear-in-time cooling composes exactly through the floor
    clamp (``max(f, max(f, x-a)-b) == max(f, x-a-b)``), so subdividing an interval
    lands a bar at the identical level. ``ticks=1`` reproduces the historical
    per-tick cooling.
    """
    decay = float(cfg.idle_decay) * max(0.0, ticks)
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
# Phase 3 — phase progression (time-driven expiry + level-driven build)       #
# --------------------------------------------------------------------------- #
def _advance_time_phase(pressure: PressureState, cfg: PressureConfig, ts: str) -> None:
    """Expire the timed phases by the wall clock — runs *before* accumulation.

    ``releasing`` -> ``aftertaste`` once the release window passes, then
    ``aftertaste`` -> ``calm`` once the aftertaste window passes (at which point
    bars are pulled most of the way down to the floor — a release should *feel*
    like relief, not a 30%% trim).

    This is deliberately applied at the *top* of a tick, before the accumulate
    gate decides whether to take inflow (finding #13). The accumulate gate keys
    off ``pressure.phase``; if an ``aftertaste`` window has *already* elapsed by
    the clock but the phase field still reads ``aftertaste``, running expiry only
    at the end of the tick would make the first post-aftertaste event fall through
    the gate and lose its inflow, its increment silently swallowed by a window
    that was already over. Expiring first means an event that arrives after the
    aftertaste has passed sees ``calm`` and accumulates normally.
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
                # Fix (2026-07-18): only settle *downward*. For bars already
                # below the floor the old formula pulled them UP to
                # floor+(cur-floor)*keep — a release could conjure phantom
                # charge in untouched bars (e.g. joy appearing after crying)
                # and pre-load the next build-up cycle.
                if cur > floor:
                    setattr(pressure.bars, k, floor + (cur - floor) * keep)


def _advance_level_phase(pressure: PressureState, cfg: PressureConfig) -> None:
    """Move ``calm`` <-> ``building`` by bar level, with hysteresis.

    ``calm`` rises to ``building`` above the build-up threshold, and ``building``
    falls back to ``calm`` below the (lower) build-down threshold. The two
    thresholds are separated so a bar hovering near the line does not flicker
    phases. Runs at the *end* of a tick, after this turn's inflow and cooling, so
    a bar that just crossed (or fell below) the line transitions on its new level.
    """
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
    elapsed_ticks: float | None = None,
) -> PressureState:
    """Advance the pressure cooker by one full tick and return it.

    A tick runs in order:

    0. **expire timed phases by the clock** — a ``releasing`` whose window has
       passed becomes ``aftertaste``; an ``aftertaste`` whose window has passed
       settles to ``calm``. This runs *first* so the accumulate gate below sees
       the phase the clock actually implies (finding #13): an event arriving after
       the aftertaste has elapsed must accumulate, not be dropped because the phase
       field still read ``aftertaste`` from a window that was already over;
    1. **accumulate** this turn's reading into the bars — but only when the
       cooker is ``calm``/``building`` (a ``releasing``/``aftertaste`` cooker
       suspends inflow, so the agent does not re-stack pressure while venting);
    2. **cool** every bar by :attr:`~feltstate.config.PressureConfig.idle_decay`,
       clamped to a trait-derived floor (always applied, every phase);
    3. **select a release** if any bar crossed
       :attr:`~feltstate.config.PressureConfig.threshold_release` (power-aware
       channel, with hybrid/collapse handling) and, if so, move into
       ``releasing``;
    4. **advance ``calm`` <-> ``building`` by bar level** (hysteresis), on the
       levels this tick's inflow and cooling produced.

    ``ts`` is the tick's ISO timestamp (its caller's clock); all release /
    aftertaste windows are computed from it, so feeding a monotonic clock keeps the
    dynamics deterministic and testable.

    ``elapsed_ticks`` is the wall-clock span since the previous tick in *reference
    ticks* (one tick = one minute; the engine threads it in from its own clock).
    The two continuous, per-time dynamics — the bar **cooldown** (step 2) and the
    **trait/tension simmer** (step 1's background drip) — scale with it, so the same
    real elapsed time cools and simmers the bars the same amount however often
    :func:`step` is called (frequency-invariance, finding #12). Per-event inflow
    (labels, valence, milestones) and the wall-clock release/aftertaste windows are
    already time-correct and are not scaled. ``None`` means exactly one reference
    tick, reproducing the historical per-tick behaviour, so a caller that does not
    thread a clock is unchanged. The passed ``pressure`` is mutated in place and
    also returned for convenience.
    """
    ts = ts or _now_iso()
    ticks = 1.0 if elapsed_ticks is None else max(0.0, float(elapsed_ticks))

    # (0) expire timed phases by the clock, BEFORE the accumulate gate (#13).
    _advance_time_phase(pressure, cfg, ts)

    # (0.5) plasticity healing — every tick, every phase: carved sensitivity
    # relaxes toward 0.5 on real elapsed time, paced by relationship.safety.
    if cfg.plasticity:
        _plast_heal(pressure, relationship, cfg, ts)

    # (1) decay + trait floor, every tick (cooling scales with elapsed time).
    #
    # This must run BEFORE the accumulate gate. Cooling is charged for the
    # elapsed time *since the last tick* — time that ran out before this turn's
    # event happened. Accumulating first meant the event was retroactively
    # decayed by that silence: a severity-1.0 shock arriving after ~22 minutes
    # of quiet contributed exactly nothing (0.40 inflow minus 0.018/min of
    # idle decay), and ordinary label inflow was erased by any gap over a
    # minute. The frequency-invariance this method promises then held only for
    # a silent tick; a tick that carried an event depended on how often step()
    # had been called during the preceding lull.
    #
    # Same shape as the phase-expiry fix above (#13): settle the clock first,
    # then apply what just happened.
    _decay_and_floor(pressure, traits, cfg, ticks)

    # (2) accumulate — only outside the vent.
    if pressure.phase not in ("releasing", "aftertaste"):
        _accumulate(
            pressure,
            delta=delta,
            traits=traits,
            relationship=relationship,
            cfg=cfg,
            ts=ts,
            ticks=ticks,
        )

    # (3) release selection — only when not already venting.
    if pressure.phase not in ("releasing", "aftertaste"):
        power = compute_power(traits, relationship, cfg)
        decision = _select_release(pressure, dials=dials, cfg=cfg, power=power, ts=ts)
        if decision is not None:
            _trigger_release(pressure, decision, cfg)

    # (4) calm <-> building by level, on this tick's resulting levels.
    _advance_level_phase(pressure, cfg)

    pressure.last_tick_ts = ts
    return pressure
