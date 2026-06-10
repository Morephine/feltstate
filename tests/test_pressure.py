"""Tests for feltstate.affect.pressure — the multi-bar pressure cooker.

The cooker accumulates per-turn inflow into five bars, fires a release when a bar
crosses threshold, then cools through an aftertaste back to a non-zero floor. The
key behaviours pinned here:

* a sustained joy signal fills the joy bar, crosses the release threshold, and
  moves the phase into ``releasing``;
* time advancing past the release + aftertaste windows returns the phase to
  ``calm`` with the bar settled to a floor (not zero);
* ``compute_power`` decides only the *channel* (express vs. suppress), never
  whether a release fires;
* accumulation is suspended while venting; idle decay always runs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from feltstate import DEFAULT_CONFIG, AffectDelta, PersonaDials, PressureState, Relationship, Traits
from feltstate.affect import compute_power, step
from feltstate.config import BAR_TO_RELEASE, BAR_TO_RELEASE_SUPPRESS

PCFG = DEFAULT_CONFIG.pressure
T0 = datetime(2030, 1, 1, 9, 0, 0, tzinfo=timezone.utc)


def _ts(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


def _high_power_state():
    """Traits/relationship that put power clearly above the express threshold,
    so a joy release comes out as the open ``burst_joy`` channel."""
    traits = Traits(optimism=0.85, depression=0.15, anxiety=0.20, curiosity=0.6)
    rel = Relationship(closeness=0.85, trust=0.85, safety=0.9)
    return traits, rel


def _low_power_state():
    """Traits/relationship that put power below the threshold -> suppression."""
    traits = Traits(optimism=0.15, depression=0.85, anxiety=0.85, curiosity=0.4)
    rel = Relationship(closeness=0.2, trust=0.2, safety=0.15)
    return traits, rel


def _drive(pressure, *, delta, traits, rel, dials, start_min=0.0, step_min=1.0, n=1):
    """Run ``n`` ticks on a monotonic clock; return the pressure (mutated)."""
    t = start_min
    for _ in range(n):
        step(
            pressure, delta=delta, traits=traits, relationship=rel, dials=dials, cfg=PCFG, ts=_ts(t)
        )
        t += step_min
    return pressure


# --------------------------------------------------------------------------- #
# compute_power                                                               #
# --------------------------------------------------------------------------- #
def test_compute_power_bounds_and_direction():
    low_t, low_r = _low_power_state()
    high_t, high_r = _high_power_state()
    p_low = compute_power(low_t, low_r, PCFG)
    p_high = compute_power(high_t, high_r, PCFG)
    assert 0.0 <= p_low <= 1.0
    assert 0.0 <= p_high <= 1.0
    # An optimistic, safe, trusting agent feels far more in control.
    assert p_high > p_low
    assert p_high > PCFG.power_threshold
    assert p_low < PCFG.power_threshold


# --------------------------------------------------------------------------- #
# Joy: build -> cross -> release -> back to calm at floor                     #
# --------------------------------------------------------------------------- #
def test_sustained_joy_builds_then_releases_burst_joy():
    traits, rel = _high_power_state()
    dials = PersonaDials()
    joy_delta = AffectDelta(valence=0.7, arousal=0.7, labels=["joyful", "excited"])
    p = PressureState()

    # The joy bar should climb as we feed joy.
    _drive(p, delta=joy_delta, traits=traits, rel=rel, dials=dials, n=10)
    assert p.bars.joy > 0.2

    # Keep feeding until it crosses the release threshold and fires.
    fired = False
    t = 10.0
    for _ in range(80):
        step(p, delta=joy_delta, traits=traits, relationship=rel, dials=dials, cfg=PCFG, ts=_ts(t))
        t += 1.0
        if p.phase == "releasing":
            fired = True
            break
    assert fired, "joy bar never crossed into a release"
    # High power -> the open channel, not the suppressed one.
    assert p.release_type == "burst_joy"
    # The bar that triggered it was at/above threshold when it fired.
    assert p.history and p.history[-1]["release_type"] == "burst_joy"


def test_release_then_time_returns_to_calm_at_nonzero_floor():
    traits, rel = _high_power_state()
    dials = PersonaDials()
    joy_delta = AffectDelta(valence=0.7, arousal=0.7, labels=["joyful", "excited"])
    p = PressureState()

    # Drive to a release.
    t = 0.0
    for _ in range(120):
        step(p, delta=joy_delta, traits=traits, relationship=rel, dials=dials, cfg=PCFG, ts=_ts(t))
        t += 1.0
        if p.phase == "releasing":
            break
    assert p.phase == "releasing"
    release_start = t

    # Now advance the clock far past the release + aftertaste windows, feeding
    # only neutral deltas (accumulation is suspended while venting anyway).
    neutral = AffectDelta(valence=0.0, arousal=0.4, labels=[])
    t = release_start + 5.0
    reached_calm = False
    for _ in range(400):
        step(p, delta=neutral, traits=traits, relationship=rel, dials=dials, cfg=PCFG, ts=_ts(t))
        t += 5.0
        if p.phase == "calm":
            reached_calm = True
            break
    assert reached_calm, "cooker never settled back to calm"

    # Settled to a floor, not to zero: joy >= bar_floor (optimism trait floor is
    # also positive here, so the bar holds a residue).
    assert p.bars.joy >= PCFG.bar_floor - 1e-6
    assert p.bars.joy > 0.0


# --------------------------------------------------------------------------- #
# Power band selects the channel, not whether to fire                         #
# --------------------------------------------------------------------------- #
def test_low_power_suppresses_the_same_release():
    """Same crossing, low power -> the *_suppress channel. (We seed the bar near
    threshold to keep the trait-floor inflow from mattering.)"""
    traits, rel = _low_power_state()
    dials = PersonaDials()
    p = PressureState()
    # Seed sadness just under threshold; one sad tick pushes it over.
    p.bars.sadness = 0.84
    sad_delta = AffectDelta(valence=-0.6, arousal=0.3, labels=["sad", "lonely"])

    fired = False
    t = 0.0
    for _ in range(20):
        step(p, delta=sad_delta, traits=traits, relationship=rel, dials=dials, cfg=PCFG, ts=_ts(t))
        t += 1.0
        if p.phase == "releasing":
            fired = True
            break
    assert fired
    # Low power -> suppressed channel.
    assert p.release_type == BAR_TO_RELEASE_SUPPRESS["sadness"]
    assert p.release_type.endswith("_suppress")


def test_high_power_expresses_open_channel_for_sadness():
    traits, rel = _high_power_state()
    dials = PersonaDials()
    p = PressureState()
    p.bars.sadness = 0.84
    sad_delta = AffectDelta(valence=-0.6, arousal=0.3, labels=["sad", "lonely"])

    fired = False
    t = 0.0
    for _ in range(20):
        step(p, delta=sad_delta, traits=traits, relationship=rel, dials=dials, cfg=PCFG, ts=_ts(t))
        t += 1.0
        if p.phase == "releasing":
            fired = True
            break
    assert fired
    assert p.release_type == BAR_TO_RELEASE["sadness"]  # "tears", open channel


# --------------------------------------------------------------------------- #
# Accumulation is suspended while venting; decay always runs                  #
# --------------------------------------------------------------------------- #
def test_no_accumulation_while_releasing():
    traits, rel = _high_power_state()
    dials = PersonaDials()
    p = PressureState()
    p.bars.joy = 0.84
    joy_delta = AffectDelta(valence=0.7, arousal=0.7, labels=["joyful", "excited"])

    # Fire a release.
    t = 0.0
    while p.phase != "releasing" and t < 20:
        step(p, delta=joy_delta, traits=traits, relationship=rel, dials=dials, cfg=PCFG, ts=_ts(t))
        t += 1.0
    assert p.phase == "releasing"

    # While releasing, keep feeding strong joy but keep time *inside* the
    # release window (short steps). The joy bar must not climb — inflow is off
    # and only decay runs.
    joy_before = p.bars.joy
    for _ in range(3):
        step(p, delta=joy_delta, traits=traits, relationship=rel, dials=dials, cfg=PCFG, ts=_ts(t))
        t += 0.2  # stay within the (>=2 min) burst_joy window
    assert p.phase == "releasing"
    assert p.bars.joy <= joy_before  # decayed or flat, never accumulated


def test_idle_decay_cools_bars_toward_floor_when_calm():
    traits = Traits()  # neutral -> all floors are 0
    rel = Relationship()
    dials = PersonaDials()
    p = PressureState()
    p.bars.anger = 0.5  # below build threshold, stays calm
    neutral = AffectDelta(valence=0.0, arousal=0.4, labels=[])

    before = p.bars.anger
    _drive(p, delta=neutral, traits=traits, rel=rel, dials=dials, n=5)
    assert p.phase == "calm"
    assert p.bars.anger < before  # cooled by idle_decay each tick


def test_chronic_temperament_keeps_a_floor_in_its_bar():
    """A high-depression temperament never lets its sadness bar cool to zero."""
    traits = Traits(depression=0.9)
    rel = Relationship()
    dials = PersonaDials()
    p = PressureState()
    p.bars.sadness = 0.6
    neutral = AffectDelta(valence=0.0, arousal=0.4, labels=[])
    _drive(p, delta=neutral, traits=traits, rel=rel, dials=dials, n=200)
    # Floor = (0.9 - 0.5) * 0.4 = 0.16; the bar must hold at/above it.
    expected_floor = (0.9 - 0.5) * 0.4
    assert p.bars.sadness >= expected_floor - 1e-6
    assert p.bars.sadness > 0.0


def test_step_mutates_and_returns_same_object():
    p = PressureState()
    out = step(
        p,
        delta=AffectDelta(),
        traits=Traits(),
        relationship=Relationship(),
        dials=PersonaDials(),
        cfg=PCFG,
        ts=_ts(0),
    )
    assert out is p
    assert p.last_tick_ts == _ts(0)


# --------------------------------------------------------------------------- #
# label_pressure_scale / agent_scale_config                                   #
# --------------------------------------------------------------------------- #
def test_label_scale_default_changes_nothing():
    """scale=1.0 must reproduce the historical companion-scale behaviour:
    per-label charge sits below idle_decay, so a label-only stream cannot
    ratchet a bar above the trait floor (here zero)."""
    traits, rel, dials = Traits(), Relationship(), PersonaDials()
    p = PressureState()
    anxious = AffectDelta(valence=-0.2, arousal=0.6, labels=["anxious"])
    _drive(p, delta=anxious, traits=traits, rel=rel, dials=dials, n=20)
    assert p.bars.anxiety == 0.0  # 0.013 charge - 0.018 decay never accumulates


def test_agent_scale_makes_mid_layer_integrate():
    """At agent scale the same label stream must accumulate (charge > decay),
    and a quiet stretch must drain it back down (no permanent ratchet)."""
    from dataclasses import replace as dc_replace

    from feltstate.config import agent_scale_config

    cfg = agent_scale_config().pressure
    traits, rel, dials = Traits(), Relationship(), PersonaDials()
    p = PressureState()
    anxious = AffectDelta(valence=-0.2, arousal=0.6, labels=["anxious"])
    for i in range(10):
        step(p, delta=anxious, traits=traits, relationship=rel, dials=dials, cfg=cfg, ts=_ts(i))
    peak = p.bars.anxiety
    # 10 failing steps at net +0.034 -> ~0.34; assert the band, not the exact value.
    assert 0.25 <= peak <= 0.45

    neutral = AffectDelta(valence=0.0, arousal=0.4, labels=[])
    for i in range(10, 22):
        step(p, delta=neutral, traits=traits, relationship=rel, dials=dials, cfg=cfg, ts=_ts(i))
    assert p.bars.anxiety < peak / 2  # decays once the signal stops

    # The factory must not mutate the shared default config.
    assert DEFAULT_CONFIG.pressure.label_pressure_scale == 1.0
    custom = dc_replace(cfg, label_pressure_scale=2.0)
    assert custom.label_pressure_scale == 2.0
