"""Tests for feltstate.affect.traits — temperament integration and decay.

The central claim of this layer is an *asymmetry*: positive traits (optimism,
curiosity) and negative ones (depression, anxiety) rise at the same speed when
fed a signal, but the positive ones relax back toward the 0.5 baseline several
times faster once the signal stops. Good moods fade; bad moods linger. These
tests pin that asymmetry and the "decays back to neutral when idle" property the
whole system depends on.
"""

from __future__ import annotations

from feltstate import DEFAULT_CONFIG, AffectDelta, Traits
from feltstate.affect import update_mood, update_traits
from feltstate.state import Mood

CFG = DEFAULT_CONFIG.traits
MCFG = DEFAULT_CONFIG.mood


def _feed(traits: Traits, labels, *, n: int = 1) -> Traits:
    """Run ``n`` ticks of the same labelled delta through update_traits."""
    delta = AffectDelta(labels=list(labels), valence=0.3, arousal=0.5)
    for _ in range(n):
        traits = update_traits(traits, delta, CFG)
    return traits


def _idle(traits: Traits, *, n: int) -> Traits:
    """Run ``n`` idle (no-signal) ticks — baseline pull only."""
    empty = AffectDelta(labels=[])
    for _ in range(n):
        traits = update_traits(traits, empty, CFG)
    return traits


def test_positive_signal_raises_optimism():
    base = Traits()
    after = _feed(base, ["joyful"], n=12)
    assert after.optimism > base.optimism
    # A clearly positive temperament shift, well above neutral.
    assert after.optimism > 0.55


def test_negative_signal_raises_depression():
    base = Traits()
    after = _feed(base, ["sad"], n=12)
    assert after.depression > base.depression
    assert after.depression > 0.55


def test_optimism_relaxes_to_baseline_faster_than_depression():
    """The headline asymmetry: after the same ramp-up and the same idle gap,
    optimism has fallen back toward 0.5 markedly more than depression has."""
    # Ramp both up to a comparable elevated level with their own signals.
    t = Traits()
    t = _feed(t, ["joyful"], n=15)  # optimism up
    t = _feed(t, ["sad"], n=15)  # depression up
    opt_peak = t.optimism
    dep_peak = t.depression
    assert opt_peak > 0.55 and dep_peak > 0.55

    # Now go quiet for a good while — only the asymmetric baseline pull runs.
    t = _idle(t, n=30)

    opt_drop = opt_peak - t.optimism
    dep_drop = dep_peak - t.depression
    # Optimism should have shed substantially more of its elevation than
    # depression (the config makes optimism's pull ~8x depression's).
    assert opt_drop > dep_drop
    # And in absolute terms optimism should be much closer to neutral.
    assert (t.optimism - 0.5) < (t.depression - 0.5)


def test_idle_ticks_decay_traits_toward_neutral():
    """With no signal, every trait eases back toward the 0.5 baseline — the
    'feelings decay back to neutral' property."""
    t = Traits(depression=0.85, optimism=0.85, anxiety=0.85, curiosity=0.85)
    t = _idle(t, n=200)  # long quiet stretch
    for name in ("depression", "optimism", "anxiety", "curiosity"):
        v = getattr(t, name)
        # Moved down toward baseline from 0.85, and not overshooting below it.
        assert 0.5 <= v < 0.85, f"{name}={v} did not relax toward baseline"


def test_idle_tick_does_not_run_ewma_only_baseline_pull():
    """An idle tick must be pull-only. If it ran the EWMA toward a zero signal
    it would drag a low trait *up* toward... nothing — and would symmetrically
    drag every trait at the same rate, erasing the asymmetry. Verify a trait
    already at baseline barely moves on an idle tick, and a low trait moves
    *up* toward 0.5 (baseline pull), not down toward 0."""
    # A trait sitting below baseline should be pulled UP toward 0.5, never
    # toward 0 (which is what a naive EWMA-toward-zero-signal would do).
    low = Traits(optimism=0.30)
    after = _idle(low, n=10)
    assert after.optimism > 0.30  # pulled up toward baseline, not down


def test_signal_then_clamp_never_pins_to_edge():
    """Even after a long saturated streak a trait stays inside the clamp, so a
    single counter-signal can always move the needle."""
    t = Traits()
    t = _feed(t, ["sad", "lonely", "numb"], n=300)
    assert t.depression <= CFG.clamp_hi
    assert t.depression < 1.0
    # One opposite signal must still be able to move it down.
    moved = _feed(t, ["joyful"], n=1)
    assert moved.depression < t.depression


def test_update_mood_runs_ewma_every_tick_and_cools_when_flat():
    """Unlike traits, mood EWMA runs every tick: a flat neutral reading cools an
    elevated mood back toward neutral rather than holding it."""
    mood = Mood(valence=0.8, arousal=0.7)
    neutral = AffectDelta(valence=0.0, arousal=0.4, labels=[])
    traits = Traits()  # even temperament -> negligible gravity
    for _ in range(30):
        mood = update_mood(mood, neutral, traits, MCFG)
    # Cooled substantially toward neutral valence.
    assert mood.valence < 0.2
    assert abs(mood.arousal - 0.4) < 0.1


def test_update_mood_trait_gravity_dims_a_depressed_temperament():
    """A depressed temperament drags the felt resting valence down: the same
    positive reading lands dimmer than it does on an even temperament."""
    reading = AffectDelta(valence=0.6, arousal=0.5, labels=["content"])

    even = Mood()
    depressed = Mood()
    even_traits = Traits()  # neutral
    dep_traits = Traits(depression=0.9)  # strongly low

    for _ in range(20):
        even = update_mood(even, reading, even_traits, MCFG)
        depressed = update_mood(depressed, reading, dep_traits, MCFG)

    # Both lifted, but the depressed temperament's felt valence is lower.
    assert depressed.valence < even.valence


def test_update_traits_returns_new_object_does_not_mutate_input():
    base = Traits(optimism=0.5)
    out = update_traits(base, AffectDelta(labels=["joyful"]), CFG)
    assert out is not base
    assert base.optimism == 0.5  # input untouched
