"""feltstate.affect.traits — slow temperament and the felt mood it pulls.

Two integrators live here, both fed by the per-turn :class:`AffectDelta` that an
:class:`~feltstate.sources.base.AffectSource` measures:

* :func:`update_traits` integrates discrete emotion labels into the four slow
  personality dimensions (:class:`~feltstate.state.Traits`) with an **asymmetric**
  EWMA: every trait *rises* at the same speed when fed, but positive traits
  (optimism, curiosity) *relax* back to the 0.5 baseline several times faster than
  the negative ones (depression, anxiety). That asymmetry is the whole model of
  "good moods fade fast, bad moods linger" — hedonic adaptation on the upside,
  rumination on the downside.

* :func:`update_mood` integrates the continuous valence/arousal reading into the
  fast :class:`~feltstate.state.Mood`, then lets ``traits`` exert *gravity*: the
  felt resting point is dragged toward the dim/bright point the temperament
  implies, so a depressed character can be cheered but never glows as brightly as
  an un-depressed one. An ``aftertaste`` term carries the previous turn's flavour
  forward so the felt state never snaps between moods.

The asymmetry hinges on one rule, shared by both integrators: **the EWMA only
runs when there is an actual signal this turn.** An idle tick (no labels / a flat
neutral reading) does *only* the baseline pull. If idle ticks ran the EWMA toward
zero-signal they would drag every trait down at the same rate and erase the
positive/negative asymmetry entirely — so idle ticks must be pull-only. This is
also what lets the whole system *decay back to neutral* when the conversation goes
quiet: keep ticking with empty deltas and traits and mood both ease home.

Every constant comes from :class:`~feltstate.config.TraitConfig` /
:class:`~feltstate.config.MoodConfig`; nothing is hard-coded or character-specific.
"""

from __future__ import annotations

from ..config import LABEL_TO_TRAITS, MoodConfig, TraitConfig
from ..state import AffectDelta, Mood, Traits

# The trait dimensions, in the canonical order used everywhere.
_TRAIT_NAMES = ("depression", "optimism", "anxiety", "curiosity")


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# --------------------------------------------------------------------------- #
# Long-term temperament                                                       #
# --------------------------------------------------------------------------- #
def _label_signals(labels: list[str]) -> dict[str, float]:
    """Collapse this turn's labels into one signal in [0, 1] per trait.

    Several labels can map to the same trait (see
    :data:`~feltstate.config.LABEL_TO_TRAITS`); we take the **max**, never the
    sum, so emitting two sad-ish labels reads as "sad" rather than "twice as sad".
    """
    signals: dict[str, float] = {}
    for label in labels or []:
        for trait, weight in LABEL_TO_TRAITS.get(label, {}).items():
            if weight > signals.get(trait, 0.0):
                signals[trait] = weight
    return signals


def update_traits(traits: Traits, delta: AffectDelta, cfg: TraitConfig) -> Traits:
    """Integrate one turn's labels into the slow personality dimensions.

    The update for each trait is, in order:

    1. **EWMA toward the signal, only if there is one.** With a signal ``s`` in
       (0, 1]::

           t <- t * (1 - alpha) + s * alpha

       On an idle tick (``s == 0``) this step is skipped entirely — see the
       module docstring for why that skip *is* the asymmetry.
    2. **Asymmetric baseline pull** back toward ``cfg.baseline`` (0.5), every
       tick, signal or not::

           t <- t * (1 - bp) + baseline * bp

       ``bp`` comes from ``cfg.baseline_pull`` per trait: small for depression /
       anxiety (they linger), several times larger for optimism / curiosity
       (they fade). A trait with no entry falls back to the smallest configured
       pull, so an unknown dimension errs on the side of stickiness rather than
       evaporating.
    3. **Hard clamp** to ``[cfg.clamp_lo, cfg.clamp_hi]`` (never the full 0/1), so
       a single counter-signal can always move the needle even after a long
       saturated streak.

    Returns a new :class:`Traits`; the input is not mutated.
    """
    signals = _label_signals(delta.labels)
    alpha = cfg.ewma_alpha
    # If a trait is absent from the pull map, fall back to the *slowest* pull so
    # an unconfigured dimension lingers rather than evaporating.
    default_pull = min(cfg.baseline_pull.values()) if cfg.baseline_pull else 0.0

    out = Traits()
    for name in _TRAIT_NAMES:
        val = getattr(traits, name)
        signal = signals.get(name, 0.0)

        # 1. EWMA — only when the turn actually carries this trait's signal.
        if signal > 0.0:
            val = val * (1.0 - alpha) + signal * alpha

        # 2. Asymmetric relaxation toward baseline (runs every tick).
        bp = cfg.baseline_pull.get(name, default_pull)
        val = val * (1.0 - bp) + cfg.baseline * bp

        # 3. Clamp away from the hard edges.
        setattr(out, name, _clamp(val, cfg.clamp_lo, cfg.clamp_hi))
    return out


# --------------------------------------------------------------------------- #
# Fast felt mood                                                              #
# --------------------------------------------------------------------------- #
def _trait_resting_point(traits: Traits) -> tuple[float, float, float]:
    """The (valence, arousal, gravity-strength) the temperament implies.

    * A depressed temperament pulls the resting valence negative; an optimistic
      one pulls it positive (depression weighted a touch heavier than optimism,
      because low moods bias the floor more than high moods raise the ceiling).
    * An anxious temperament raises the resting arousal.
    * Gravity strength scales with how far the *most* deviant trait sits from
      0.5: near baseline there is essentially no pull, so an even temperament
      lets the felt state move freely; a strongly-coloured temperament drags it
      home harder. A small dead-zone keeps tiny deviations from biasing anything.
    """
    depression = traits.depression
    optimism = traits.optimism
    anxiety = traits.anxiety

    resting_v = -max(0.0, depression - 0.5) * 0.40 + max(0.0, optimism - 0.5) * 0.30
    resting_a = 0.5 + max(0.0, anxiety - 0.5) * 0.20

    deviation = max(abs(depression - 0.5), abs(optimism - 0.5), abs(anxiety - 0.5))
    # Dead-zone of 0.05, then ramp; capped so gravity can soften but never pin.
    strength = min(0.20, max(0.0, deviation - 0.05) * 0.6)
    return resting_v, resting_a, strength


def update_mood(mood: Mood, delta: AffectDelta, traits: Traits, cfg: MoodConfig) -> Mood:
    """Integrate one turn's continuous reading into the fast felt mood.

    Steps, in order:

    1. **Aftertaste blend.** Before moving, the *new* reading is softened by
       whatever lingering flavour the previous turn left in ``mood.aftertaste``,
       weighted by ``cfg.aftertaste_weight``. This keeps the felt state from
       snapping when consecutive readings disagree.
    2. **Felt valence/arousal EWMA** toward that blended reading, at
       ``cfg.va_alpha`` (faster than traits — mood is the quick layer)::

           v <- v * (1 - va_alpha) + reading_v * va_alpha

       Unlike traits, this runs every tick (valence/arousal are already-bounded
       continuous signals, so a flat reading is itself informative — it cools the
       mood toward neutral rather than holding it).
    3. **Trait gravity.** The felt point is then dragged toward the resting point
       the temperament implies (see :func:`_trait_resting_point`), scaled by
       ``cfg.trait_gravity``. This is what makes a depressed character's good
       moods land dimmer than an even-tempered one's.
    4. **Record the new aftertaste** — the flavour this turn leaves for the next.

    ``labels`` are copied straight from the reading (the discrete felt tags the
    renderer shows); the continuous v/a are smoothed. Returns a new :class:`Mood`;
    the input is not mutated.
    """
    # 1. Soften the incoming reading with last turn's lingering flavour.
    reading_v = delta.valence
    reading_a = delta.arousal
    after = mood.aftertaste
    if isinstance(after, dict):
        w = float(after.get("weight", cfg.aftertaste_weight))
        w = _clamp(w, 0.0, 1.0)
        reading_v = reading_v * (1.0 - w) + float(after.get("valence", reading_v)) * w
        reading_a = reading_a * (1.0 - w) + float(after.get("arousal", reading_a)) * w

    # 2. Felt EWMA toward the blended reading (runs every tick).
    va = cfg.va_alpha
    target_v = mood.valence * (1.0 - va) + reading_v * va
    felt_a = mood.arousal * (1.0 - va) + reading_a * va

    # A1: negative-channel momentum. A dip carries inertia — it overshoots its
    # target and recovers slowly, the way a bad mood doesn't lift the instant the
    # cause passes (a sulk has a trough). Good moods, by contrast, stay on the
    # plain fast EWMA. Off when momentum_mu == 0 → identical to the
    # bare EWMA. mu is held below the range where momentum would stop recovering.
    mu = _clamp(cfg.momentum_mu, 0.0, 0.9)
    velocity = mood.velocity
    if mu > 0.0:
        new_velocity = mu * velocity + (1.0 - mu) * (target_v - mood.valence)
        rising_in_positive = mood.valence >= 0.0 and target_v >= mood.valence
        if cfg.momentum_negative_only and rising_in_positive:
            felt_v = target_v  # bright side: plain fast EWMA, no momentum
            velocity = 0.0
        else:
            felt_v = mood.valence + new_velocity  # carry the downswing / slow climb
            velocity = new_velocity
    else:
        felt_v = target_v
        velocity = 0.0

    # 3. Trait gravity — drag the felt point toward the temperament's resting
    #    point. trait_gravity scales the per-tick pull so the effect accrues
    #    smoothly over several ticks rather than teleporting.
    resting_v, resting_a, dev_strength = _trait_resting_point(traits)
    g = _clamp(cfg.trait_gravity * dev_strength, 0.0, 1.0)
    if g > 0.0:
        felt_v = felt_v - (felt_v - resting_v) * g
        felt_a = felt_a - (felt_a - resting_a) * g

    felt_v = _clamp(felt_v, -1.0, 1.0)
    felt_a = _clamp(felt_a, 0.0, 1.0)

    # 4. The flavour this turn leaves behind for the next turn's blend.
    new_aftertaste = {
        "valence": round(felt_v, 4),
        "arousal": round(felt_a, 4),
        "weight": round(_clamp(cfg.aftertaste_weight, 0.0, 1.0), 4),
    }

    return Mood(
        valence=felt_v,
        arousal=felt_a,
        labels=list(delta.labels),
        aftertaste=new_aftertaste,
        mixed_blend=delta.mixed_blend,
        velocity=round(velocity, 4),
    )
