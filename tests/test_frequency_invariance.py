"""Frequency-invariance of the elapsed-time decays (findings #11-#16).

The adapted companion prototype decays by *real elapsed wall-clock time*, not by how
often it is ticked. feltstate now matches that: the trait baseline pull, the
pressure bar cooldown + trait/tension simmer, the relationship tension decay, the
tiredness accumulator, and the dream residue are all functions of elapsed time.
The guarantee these tests pin: **the same real elapsed time yields approximately
the same state regardless of tick cadence.**

The headline test replays the *same* 24 hours — the same events at the same
wall-clock instants — at a 1-minute cadence and at a 5-minute cadence, and
asserts the final mood / traits / relationship / pressure / tiredness match. The
rest are focused unit tests for the ordering fix (#13) and the tiredness
prior-interval / self-accel integration (#14, #15) and the tracked dream residue
(#16).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from feltstate import (
    DEFAULT_CONFIG,
    AffectDelta,
    PersonaDials,
    PressureState,
    Relationship,
    Tiredness,
    TirednessConfig,
    Traits,
)
from feltstate.affect import step, update_traits
from feltstate.engine import Engine
from feltstate.sources.base import AffectSource, latest_user_text

T0 = datetime(2030, 6, 1, 8, 0, 0)  # naive local, like the engine's own clock


# --------------------------------------------------------------------------- #
# A deterministic, wall-clock-scripted source                                 #
# --------------------------------------------------------------------------- #
# Events are placed on the 5-minute grid so BOTH cadences tick exactly on them
# and apply the identical event at the identical wall-time; every other tick is a
# flat, confident, cue-less idle reading (pure decay). Because the source keys off
# the injected `now` — not off call count — the two cadences see the same history.
_EVENTS: dict[int, AffectDelta] = {
    0: AffectDelta(
        valence=-0.6,
        arousal=0.7,
        labels=["sad", "anxious"],
        confidence=0.9,
        milestones=[{"kind": "conflict", "severity": 0.8, "actor": "user"}],
    ),
    5: AffectDelta(
        valence=-0.5,
        arousal=0.65,
        labels=["frustrated"],
        confidence=0.9,
    ),
    10: AffectDelta(
        valence=0.6,
        arousal=0.6,
        labels=["grateful", "content"],
        confidence=0.9,
        milestones=[{"kind": "repair", "severity": 0.7, "actor": "user"}],
    ),
}


class ScriptedSource(AffectSource):
    """Emit a scripted event at fixed wall-clock minute offsets from ``T0``; a
    flat, confident, idle reading otherwise. Keyed on the message's embedded
    minute offset so it is identical across tick cadences (never call-count)."""

    def read(self, messages, *, baseline, persona: str = "") -> AffectDelta:
        text = latest_user_text(messages)
        try:
            minute = int(text.split("@", 1)[1])
        except (IndexError, ValueError):
            minute = -1
        ev = _EVENTS.get(minute)
        if ev is None:
            # Confident idle: an intentional flat reading (not a low-confidence
            # error), so it is trusted and cools the state rather than being
            # neutralised — it carries no labels/milestones, so it is pure decay.
            return AffectDelta(valence=0.0, arousal=0.4, labels=[], confidence=0.9)
        # Return a fresh copy so the engine's history can't mutate the script.
        return AffectDelta.from_dict(ev.to_dict())


def _run_cadence(tmp_path, step_min: int, span_min: int = 24 * 60) -> Engine:
    """Drive an engine from T0 for ``span_min`` minutes at a fixed cadence.

    Every tick is stamped with an explicit ``now`` so decay integrates real
    elapsed time. The user message encodes the current minute offset, so the
    scripted source fires the same event at the same wall-time in every cadence.
    """
    eng = Engine(source=ScriptedSource(), state_path=tmp_path / f"s_{step_min}.json")
    minute = 0
    while minute <= span_min:
        now = T0 + timedelta(minutes=minute)
        eng.tick([{"role": "user", "content": f"msg @{minute}"}], now=now)
        minute += step_min
    return eng


def _assert_close(a: float, b: float, tol: float, what: str) -> None:
    assert abs(a - b) <= tol, f"{what}: {a!r} vs {b!r} (|Δ|={abs(a - b):.5f} > {tol})"


# --------------------------------------------------------------------------- #
# The headline guarantee: 1-min vs 5-min over the same 24h                     #
# --------------------------------------------------------------------------- #
def test_same_24h_at_1min_and_5min_cadence_match(tmp_path):
    fine = _run_cadence(tmp_path, step_min=1)
    coarse = _run_cadence(tmp_path, step_min=5)

    f, c = fine.state, coarse.state

    # Mood — the fast layer converges to the same steady state at both cadences.
    _assert_close(f.mood.valence, c.mood.valence, 0.02, "mood.valence")
    _assert_close(f.mood.arousal, c.mood.arousal, 0.02, "mood.arousal")

    # Traits — the baseline pull is an exponential in elapsed time, so the same
    # total elapsed decays each trait the same amount however finely it is ticked.
    for name in ("depression", "optimism", "anxiety", "curiosity"):
        _assert_close(getattr(f.traits, name), getattr(c.traits, name), 0.01, f"traits.{name}")

    # Relationship — warm/cold drift is per-event (same events, same times) and
    # tension decay is elapsed-time; both land together.
    _assert_close(f.relationship.closeness, c.relationship.closeness, 0.01, "closeness")
    _assert_close(f.relationship.trust, c.relationship.trust, 0.01, "trust")
    _assert_close(f.relationship.safety, c.relationship.safety, 0.01, "safety")
    _assert_close(
        f.relationship.unresolved_tension, c.relationship.unresolved_tension, 0.01, "tension"
    )

    # Pressure — per-event inflow at matched times, elapsed-scaled cooldown/simmer
    # between; the bars settle to the same levels and the same phase.
    for bar in ("sadness", "anger", "anxiety", "boundary", "joy"):
        _assert_close(
            getattr(f.pressure.bars, bar), getattr(c.pressure.bars, bar), 0.01, f"bars.{bar}"
        )
    assert f.pressure.phase == c.pressure.phase

    # Tiredness — arousal×elapsed integrated in closed form; cadence-independent.
    _assert_close(fine.tiredness.level, coarse.tiredness.level, 0.02, "tiredness.level")


def test_invariance_holds_mid_transient_not_only_at_rest(tmp_path):
    """The 24h test lets everything settle back to baseline; this one stops at 90
    minutes — events at 0/5/10 min, then still visibly decaying — so it proves the
    *slow* elapsed-time decays (traits, relationship, pressure) are frequency-
    invariant *while in flight*, not just once converged. The mood is the fast,
    per-event EWMA (deliberately not elapsed-scaled: it tracks the current
    reading), so mid-transient it may differ by more than the slow layers; over a
    full day it converges, which the headline test pins tightly."""
    fine = _run_cadence(tmp_path, step_min=1, span_min=90)
    coarse = _run_cadence(tmp_path, step_min=5, span_min=90)
    f, c = fine.state, coarse.state

    # Slow, elapsed-time-decayed layers: tightly invariant even mid-decay.
    for name in ("depression", "optimism", "anxiety", "curiosity"):
        _assert_close(getattr(f.traits, name), getattr(c.traits, name), 0.005, f"traits.{name}")
    _assert_close(f.relationship.closeness, c.relationship.closeness, 0.005, "closeness")
    _assert_close(f.relationship.safety, c.relationship.safety, 0.005, "safety")
    _assert_close(
        f.relationship.unresolved_tension, c.relationship.unresolved_tension, 0.005, "tension"
    )
    for bar in ("sadness", "anger", "anxiety", "boundary", "joy"):
        _assert_close(
            getattr(f.pressure.bars, bar), getattr(c.pressure.bars, bar), 0.005, f"bars.{bar}"
        )
    # The traits are genuinely still elevated here (not yet decayed to baseline),
    # so this is a real mid-transient check, not a settled one.
    assert f.traits.depression > 0.505


def test_naive_per_tick_would_diverge_but_elapsed_does_not(tmp_path):
    """Guard the guarantee against regressing to per-tick decay: at very different
    cadences the *idle decay of a pressure bar* over a fixed span must match. A
    per-tick-count cooldown would drain the 1-min run ~5x further than the 5-min
    run over the same two hours; the elapsed-time cooldown drains them equally."""

    def _cool(step_min: int) -> float:
        p = PressureState()
        p.bars.anger = 0.9  # start high, feed nothing, let it cool
        traits, rel, dials = Traits(), Relationship(), PersonaDials()
        minute = 0
        prev = None
        while minute <= 120:  # two hours
            ts = (T0 + timedelta(minutes=minute)).isoformat()
            elapsed = None if prev is None else (minute - prev)
            step(
                p,
                delta=AffectDelta(valence=0.0, arousal=0.4, labels=[]),
                traits=traits,
                relationship=rel,
                dials=dials,
                cfg=DEFAULT_CONFIG.pressure,
                ts=ts,
                elapsed_ticks=elapsed,
            )
            prev = minute
            minute += step_min
        return p.bars.anger

    fine = _cool(1)
    coarse = _cool(5)
    # Same real elapsed time -> same cooled level (frequency-invariant).
    _assert_close(fine, coarse, 1e-6, "anger after 2h idle")
    # And it genuinely cooled from 0.9 by ~ idle_decay * 120 minutes.
    assert fine < 0.9


# --------------------------------------------------------------------------- #
# #11: traits baseline pull is elapsed-time and composes                       #
# --------------------------------------------------------------------------- #
def test_traits_baseline_pull_is_elapsed_time_and_composes():
    cfg = DEFAULT_CONFIG.traits
    start = Traits(optimism=0.9)
    idle = AffectDelta(labels=[])

    # One 10-tick relaxation vs ten 1-tick relaxations must land identically
    # (exponential-in-elapsed composes exactly).
    one_step = update_traits(start, idle, cfg, elapsed_ticks=10.0)
    many = start
    for _ in range(10):
        many = update_traits(many, idle, cfg, elapsed_ticks=1.0)
    _assert_close(one_step.optimism, many.optimism, 1e-9, "10 ticks one-shot vs stepwise")

    # elapsed_ticks=None reproduces the historical single-tick relaxation exactly.
    legacy = (
        start.optimism * (1.0 - cfg.baseline_pull["optimism"]) + 0.5 * cfg.baseline_pull["optimism"]
    )
    default = update_traits(start, idle, cfg)  # None -> one tick
    _assert_close(default.optimism, legacy, 1e-12, "None == one legacy tick")

    # A longer elapsed span relaxes strictly further toward baseline.
    short = update_traits(start, idle, cfg, elapsed_ticks=1.0)
    long = update_traits(start, idle, cfg, elapsed_ticks=30.0)
    assert (long.optimism - 0.5) < (short.optimism - 0.5)


# --------------------------------------------------------------------------- #
# #13: an expired aftertaste must not swallow the next event's increment        #
# --------------------------------------------------------------------------- #
def _mk_state_in_aftertaste(until_offset_min: float) -> PressureState:
    p = PressureState()
    p.phase = "aftertaste"
    p.release_type = "tears"
    p.release_started_ts = T0.isoformat()
    p.release_ends_ts = T0.isoformat()
    p.aftertaste_until_ts = (T0 + timedelta(minutes=until_offset_min)).isoformat()
    p.last_tick_ts = T0.isoformat()
    return p


def _step_expired_aftertaste(delta: AffectDelta) -> PressureState:
    """Run one tick where the aftertaste window has already elapsed (ends +30 min,
    tick at +45 min), returning the resulting pressure. Bars start at the floor so
    the aftertaste->calm settle (which pulls bars toward the floor) is a no-op and
    cannot masquerade as accumulation."""
    cfg = DEFAULT_CONFIG.pressure
    traits, rel, dials = Traits(), Relationship(), PersonaDials()
    p = _mk_state_in_aftertaste(30.0)
    floor = cfg.bar_floor
    for k in ("sadness", "anger", "anxiety", "boundary", "joy"):
        setattr(p.bars, k, floor)  # at the floor: settle is a no-op here
    step(
        p,
        delta=delta,
        traits=traits,
        relationship=rel,
        dials=dials,
        cfg=cfg,
        ts=(T0 + timedelta(minutes=45)).isoformat(),
    )
    return p


def test_expired_aftertaste_first_event_still_accumulates():
    """Finding #13: when the aftertaste window has already elapsed by the clock,
    the very first event afterward must accumulate — the stale phase must be
    expired *before* the accumulate gate reads it, not after.

    Isolated by contrast: run the expired-aftertaste tick once with an anxious
    event and once with a neutral one. The *difference* in the anxiety bar is
    exactly this event's inflow, which only survives if the phase was expired to
    calm before the accumulate gate. (Both runs also get the same
    aftertaste->calm settle, so the settle cancels out of the comparison — the
    buggy ordering, which drops the inflow, leaves the two runs equal.)"""
    strong_anxious = AffectDelta(
        valence=-0.3, arousal=0.6, labels=["anxious", "scared"], confidence=0.9
    )
    neutral = AffectDelta(valence=0.0, arousal=0.4, labels=[], confidence=0.9)

    p_event = _step_expired_aftertaste(strong_anxious)
    p_idle = _step_expired_aftertaste(neutral)

    # The aftertaste was expired first, so the anxious event accumulated; the
    # neutral run did not. The gap is the event's inflow that the old ordering
    # (expire only at end-of-tick) would have swallowed.
    assert p_event.phase in ("calm", "building")
    assert p_event.bars.anxiety > p_idle.bars.anxiety + 1e-6, (
        "expired aftertaste swallowed the first event's increment"
    )


def test_unexpired_aftertaste_still_suspends_accumulation():
    """The complement of #13: while the aftertaste window is genuinely still open,
    accumulation stays suspended (you don't re-stack pressure while venting)."""
    cfg = DEFAULT_CONFIG.pressure
    traits, rel, dials = Traits(), Relationship(), PersonaDials()
    anxious = AffectDelta(valence=-0.3, arousal=0.6, labels=["anxious"], confidence=0.9)

    # Aftertaste ends at +30 min; the event arrives at +10 min (still inside).
    p = _mk_state_in_aftertaste(30.0)
    anxiety_before = p.bars.anxiety
    step(
        p,
        delta=anxious,
        traits=traits,
        relationship=rel,
        dials=dials,
        cfg=cfg,
        ts=(T0 + timedelta(minutes=10)).isoformat(),
    )
    assert p.phase == "aftertaste"
    # No accumulation while venting; the bar only cooled (or held at floor).
    assert p.bars.anxiety <= anxiety_before


# --------------------------------------------------------------------------- #
# #14: tiredness integrates the PRIOR interval at the PRIOR arousal             #
# --------------------------------------------------------------------------- #
def test_tiredness_uses_prior_interval_arousal_not_the_new_one():
    """Finding #14: calm for 10 hours then one high-arousal message must NOT
    retroactively integrate those 10 hours at the high arousal. The elapsed
    interval is charged at the arousal that was in effect during it."""
    cfg = TirednessConfig()  # rise_k = 0.125, self_accel off
    t = Tiredness()
    t.rise(0.1, T0, cfg)  # stamp; the coming 10h are "lived" at arousal 0.1
    t.rise(0.9, T0 + timedelta(hours=10), cfg)  # a burst arrives after 10 calm hours

    # Correct: 10h integrated at the PRIOR arousal 0.1 -> 0.125 * 0.1 * 10 = 0.125.
    _assert_close(t.level, 0.125, 1e-9, "10h charged at prior (calm) arousal")

    # The buggy behaviour would charge 10h at the NEW arousal 0.9 -> 1.125 (9x).
    assert t.level < 0.9, "prior-interval arousal was ignored (retroactive high-arousal charge)"

    # And the just-set arousal governs the NEXT interval.
    t.rise(0.9, T0 + timedelta(hours=11), cfg)  # one more hour, now at 0.9
    _assert_close(t.level, 0.125 + 0.125 * 0.9 * 1.0, 1e-9, "next hour charged at 0.9")


# --------------------------------------------------------------------------- #
# #15: self-accelerating tiredness is frequency-invariant                       #
# --------------------------------------------------------------------------- #
def test_tiredness_self_accel_is_frequency_invariant():
    """Finding #15: with self-acceleration on, integrating a fixed span in one big
    step must equal integrating it in many small steps (closed-form ODE solution,
    not a call-count-dependent Euler sum)."""
    cfg = TirednessConfig(self_accel_alpha=1.0)  # strong compounding

    one = Tiredness()
    one.rise(0.8, T0, cfg)  # stamp, arousal for the whole span = 0.8
    one.rise(0.8, T0 + timedelta(hours=6), cfg)  # 6h in a single step

    many = Tiredness()
    many.rise(0.8, T0, cfg)
    for h in range(1, 6 * 12 + 1):  # 6h in 5-minute steps
        many.rise(0.8, T0 + timedelta(minutes=5 * h), cfg)

    _assert_close(one.level, many.level, 1e-6, "self-accel one-shot vs stepwise")
    # Self-acceleration actually compounded above the plain linear amount.
    assert one.level > cfg.rise_k * 0.8 * 6.0


def test_tiredness_self_accel_off_is_plain_linear():
    """With self_accel_alpha == 0 the closed form degenerates to L += c·dt, and it
    is still frequency-invariant (linear-in-time also composes)."""
    cfg = TirednessConfig()  # alpha 0
    one = Tiredness()
    one.rise(0.5, T0, cfg)
    one.rise(0.5, T0 + timedelta(hours=4), cfg)

    many = Tiredness()
    many.rise(0.5, T0, cfg)
    for h in range(1, 4 + 1):
        many.rise(0.5, T0 + timedelta(hours=h), cfg)

    _assert_close(one.level, many.level, 1e-9, "linear one-shot vs stepwise")
    _assert_close(one.level, cfg.rise_k * 0.5 * 4.0, 1e-9, "plain arousal×elapsed")


# --------------------------------------------------------------------------- #
# #16: dream residue is a tracked, decaying value (not inferred from the mood)  #
# --------------------------------------------------------------------------- #
def _dream_engine(tmp_path):
    import random

    from feltstate.dream import Fragment
    from feltstate.sources.keyword import KeywordSource

    eng = Engine(source=KeywordSource(), state_path=tmp_path / "s.json")
    eng.tiredness.level = 1.5
    d = eng.maybe_dream(
        idle_minutes=60,
        now=T0,
        fragments=[Fragment("x", 0.7, 0.6), Fragment("y", 0.6, 0.5), Fragment("z", 0.6, 0.5)],
        rng=random.Random(0),
    )
    assert d is not None and d.text != ""
    assert eng._last_dream == d.text
    assert eng._dream_residue > 0.0  # the residue was seeded by the dream's nudge
    return eng, d


def test_dream_residue_survives_an_unrelated_negative_mood(tmp_path):
    """Finding #16: an unrelated mood must not instantly cancel a fresh dream. A
    *negative* conversation right after a (positive) dream drags the total mood the
    opposite way from the dream's residue — the old logic keyed off "is the total
    mood near neutral", which this crossing-through-neutral would trip. The tracked
    residue instead keeps the dream remembered until real elapsed time spends it."""
    eng, d = _dream_engine(tmp_path)
    # A few sad turns, timestamped seconds apart (≈ one reference tick each): the
    # residue decays only a little, so the dream text is still held even as the
    # mood swings negative (through neutral, where the old check would misfire).
    now = T0
    for _ in range(4):
        now = now + timedelta(seconds=5)
        eng.tick([{"role": "user", "content": "i feel so sad and lonely and hurt"}], now=now)
    assert eng.state.mood.valence < 0.0  # mood swung the OPPOSITE way from the dream
    assert eng._last_dream == d.text  # ...yet the dream is still remembered
    assert eng._dream_residue > 0.0


def test_dream_residue_decays_over_elapsed_time_and_forgets(tmp_path):
    """The residue decays on the elapsed-time clock; once spent, the dream text is
    forgotten — regardless of what the mood happens to be doing."""
    eng, d = _dream_engine(tmp_path)
    # A single tick a long time later: the residue has decayed away by elapsed
    # time, so the dream is forgotten in one step (no need to hammer many ticks).
    eng.tick(
        [{"role": "user", "content": "the wooden table is brown"}], now=T0 + timedelta(hours=6)
    )
    assert eng._dream_residue == 0.0
    assert eng._last_dream == ""


def test_dream_residue_persists_across_reload(tmp_path):
    """The tracked residue is bookkeeping the engine must not lose on restart, or a
    reloaded companion would mis-time when it forgets the dream."""
    from feltstate.sources.keyword import KeywordSource

    eng, d = _dream_engine(tmp_path)
    saved = eng._dream_residue
    assert saved > 0.0
    reloaded = Engine(source=KeywordSource(), state_path=tmp_path / "s.json")
    _assert_close(reloaded._dream_residue, round(saved, 6), 1e-9, "residue round-trips")
    assert reloaded._last_dream == d.text


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
