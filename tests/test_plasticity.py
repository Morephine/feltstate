"""Plasticity — what fires, sensitizes; what is safe, heals.

Per-bar sensitivity is carved by micro hits whenever a tick's raw inflow
clears a charge threshold, multiplies subsequent inflow through a gain around
the 0.5 baseline, and heals toward 0.5 at a small daily percentage paced by
relationship.safety. Character timescale ~180 days; no single message moves
anything perceptibly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from feltstate.affect.pressure import (
    _plast_gain,
    _plast_heal,
    _plast_register_hits,
    _sens_of,
    step,
)
from feltstate.config import PersonaDials, PressureConfig
from feltstate.state import AffectDelta, PressureState, Relationship, Traits

CFG = PressureConfig()
T0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# --------------------------------------------------------------------------- #
# Hit metering                                                                 #
# --------------------------------------------------------------------------- #
def test_hits_two_grades_and_a_floor():
    p = PressureState()
    _plast_register_hits(p, {"joy": 0.01, "anger": 0.1, "sadness": 0.005}, CFG)
    assert p.sensitivity["joy"] == round(0.5 + CFG.plast_hit_light, 8)
    assert p.sensitivity["anger"] == round(0.5 + CFG.plast_hit_heavy, 8)
    assert "sadness" not in p.sensitivity  # below light charge: carves nothing


def test_hits_ignore_non_finite_and_cap_at_ceiling():
    p = PressureState()
    _plast_register_hits(p, {"joy": float("nan"), "anger": float("inf")}, CFG)
    assert p.sensitivity == {}
    p.sensitivity["joy"] = CFG.plast_ceil
    _plast_register_hits(p, {"joy": 0.02}, CFG)
    assert p.sensitivity["joy"] == CFG.plast_ceil  # capped, never past the ceil


def test_micro_scale_survives_rounding():
    # 8-decimal rounding must not quantize a light hit away (the dead-zone fix).
    p = PressureState()
    _plast_register_hits(p, {"joy": 0.01}, CFG)
    assert p.sensitivity["joy"] > 0.5


# --------------------------------------------------------------------------- #
# Gain                                                                         #
# --------------------------------------------------------------------------- #
def test_gain_is_linear_around_baseline():
    p = PressureState()
    assert _plast_gain(p, "joy", CFG) == 1.0  # untouched bar: neutral
    p.sensitivity["joy"] = 0.9
    assert abs(_plast_gain(p, "joy", CFG) - (1.0 + CFG.plast_gain_k * 0.4)) < 1e-12
    p.sensitivity["joy"] = 0.2
    assert abs(_plast_gain(p, "joy", CFG) - (1.0 - CFG.plast_gain_k * 0.3)) < 1e-12


def test_sens_of_defends_against_bad_values():
    p = PressureState()
    p.sensitivity["joy"] = float("nan")
    assert _sens_of(p, "joy", CFG) == 0.5
    p.sensitivity["joy"] = 5.0
    assert _sens_of(p, "joy", CFG) == CFG.plast_ceil


# --------------------------------------------------------------------------- #
# Healing                                                                      #
# --------------------------------------------------------------------------- #
def test_first_sighting_only_plants_the_anchor():
    p = PressureState()
    p.sensitivity["joy"] = 0.6
    _plast_heal(p, Relationship(), CFG, _iso(T0))
    assert p.sensitivity["joy"] == 0.6  # nothing healed yet
    assert p.sens_last_decay_ts is not None


def test_healing_paced_by_safety():
    def healed(safety: float) -> float:
        p = PressureState()
        p.sensitivity["joy"] = 0.6
        _plast_heal(p, Relationship(), CFG, _iso(T0))
        _plast_heal(p, Relationship(safety=safety), CFG, _iso(T0 + timedelta(days=30)))
        return p.sensitivity["joy"]

    slow, fast = healed(0.0), healed(1.0)
    assert fast < slow < 0.6  # both heal toward 0.5; a safe bond heals faster
    expected_fast = 0.5 + 0.1 * (1.0 - CFG.plast_decay_hi) ** 30
    assert abs(fast - expected_fast) < 1e-8


def test_healing_is_frequency_invariant():
    def run(steps: int) -> float:
        p = PressureState()
        p.sensitivity["anger"] = 0.7
        _plast_heal(p, Relationship(), CFG, _iso(T0))
        span = timedelta(days=20)
        for i in range(1, steps + 1):
            _plast_heal(p, Relationship(), CFG, _iso(T0 + span * i / steps))
        return p.sensitivity["anger"]

    assert abs(run(1) - run(20)) < 1e-6  # same span, any tick count


# --------------------------------------------------------------------------- #
# Through step(): raw-inflow metering, gain on commit, master switch           #
# --------------------------------------------------------------------------- #
def _one_joy_tick(p: PressureState, cfg: PressureConfig, ts: datetime) -> None:
    step(
        p,
        delta=AffectDelta(labels=["joyful"], valence=0.0),
        traits=Traits(),
        relationship=Relationship(),
        dials=PersonaDials(),
        cfg=cfg,
        ts=_iso(ts),
    )


def test_step_meters_hits_and_amplifies_inflow():
    p = PressureState()
    _one_joy_tick(p, CFG, T0)
    assert p.sensitivity.get("joy", 0.5) > 0.5  # the label charge cleared light

    # a carved-up bar takes more from the same stimulus than a neutral one
    hot, cold = PressureState(), PressureState()
    hot.sensitivity["joy"] = 0.9
    _one_joy_tick(hot, CFG, T0)
    _one_joy_tick(cold, CFG, T0)
    assert hot.bars.joy > cold.bars.joy


def test_master_switch_is_a_true_no_op():
    p = PressureState()
    _one_joy_tick(p, PressureConfig(plasticity=False), T0)
    assert p.sensitivity == {}
    assert p.sens_last_decay_ts is None


def test_sensitivity_round_trips_serialization():
    p = PressureState()
    p.sensitivity["joy"] = 0.50001234
    p.sens_last_decay_ts = _iso(T0)
    q = PressureState.from_dict(p.to_dict())
    assert q.sensitivity == p.sensitivity
    assert q.sens_last_decay_ts == p.sens_last_decay_ts
