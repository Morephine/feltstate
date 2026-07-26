"""Pins for the 2026-07-18 external-review fixes.

Five findings: the milestone tuning table lived as hardcoded constants in
logic; the anticipation joy floor coupled to idle_decay through a bare 0.018;
Canon's O(n) flat-file scale was undocumented; canon's cross-process lock
degraded silently on non-Unix platforms; render() hardcoded the wall clock
while tick() accepted an injectable one.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from feltstate import AffectDelta, PersonaDials, PressureState, Relationship, Traits
from feltstate.affect import pressure as pressure_mod
from feltstate.affect import step
from feltstate.affect.pressure import _apply_milestone
from feltstate.config import (
    MILESTONE_ALIASES,
    MILESTONE_EFFECTS,
    PressureConfig,
)
from feltstate.engine import Engine
from feltstate.memory import canon as canon_mod
from feltstate.memory.canon import Canon
from feltstate.sources.keyword import KeywordSource


def _zero_inflow() -> dict:
    return {"sadness": 0.0, "anger": 0.0, "anxiety": 0.0, "boundary": 0.0, "joy": 0.0}


# --------------------------------------------------------------------------- #
# 1. Milestone table lives in config, and the interpreter matches the old      #
#    hardcoded behaviour exactly.                                              #
# --------------------------------------------------------------------------- #
def test_milestone_table_lives_in_config():
    # The table is data in feltstate.config, same idiom as LABEL_TO_PRESSURE.
    assert "conflict" in MILESTONE_EFFECTS
    assert MILESTONE_ALIASES["trauma_betrayal"] == "betrayal"


def test_milestone_interpreter_matches_legacy_numbers():
    # conflict: flat adds
    inflow = _zero_inflow()
    _apply_milestone(inflow, {"kind": "conflict"})
    assert inflow["anger"] == 0.025 and inflow["sadness"] == 0.015

    # betrayal at severity 1.0: severity-scaled adds + joy knockdown clamped at 0
    inflow = _zero_inflow()
    inflow["joy"] = 0.10
    _apply_milestone(inflow, {"kind": "trauma_betrayal", "severity": 1.0})
    assert inflow["sadness"] == 0.30 and inflow["anger"] == 0.25
    assert inflow["boundary"] == 0.20
    assert abs(inflow["joy"] - max(0.0, 0.10 - 0.15)) < 1e-12

    # care from the *user*: adds joy and damps this turn's negative inflow
    inflow = _zero_inflow()
    inflow["sadness"] = 0.10
    _apply_milestone(inflow, {"kind": "care", "actor": "user"})
    assert inflow["joy"] == 0.04
    assert abs(inflow["sadness"] - 0.06) < 1e-12

    # care from anyone else: actor gate holds, nothing moves
    inflow = _zero_inflow()
    inflow["sadness"] = 0.10
    _apply_milestone(inflow, {"kind": "care", "actor": "agent"})
    assert inflow["joy"] == 0.0 and inflow["sadness"] == 0.10

    # unknown kind: no effect, no crash
    inflow = _zero_inflow()
    _apply_milestone(inflow, {"kind": "nonsense"})
    assert inflow == _zero_inflow()


def test_milestone_table_is_overridable(monkeypatch):
    monkeypatch.setitem(pressure_mod.MILESTONE_EFFECTS, "conflict", {"add": {"anger": 0.5}})
    inflow = _zero_inflow()
    _apply_milestone(inflow, {"kind": "conflict"})
    assert inflow["anger"] == 0.5 and inflow["sadness"] == 0.0


# --------------------------------------------------------------------------- #
# 2. Anticipation floor: both knobs named; defaults reproduce the old 0.018    #
#    formula bit-for-bit.                                                      #
# --------------------------------------------------------------------------- #
def test_anticipation_knobs_default_to_legacy_formula():
    """The named knobs must actually drive the engine, not just exist.

    This used to compute both sides of its own assertion from PressureConfig()
    and never call production code: hardcoding 0.5 and 0.018 back into
    pressure.py left it green. It pins the defaults, not the wiring. Now it
    drives step() and checks the joy floor the engine actually applies, and
    that turning the knob moves it.
    """
    a_v, a_w = 0.8, 0.6
    ant = AffectDelta(
        valence=0.0,
        arousal=0.3,
        confidence=0.05,  # below the trust floor: only the anticipation acts
        anticipation={"valence": a_v, "weight": a_w},
    )

    def floor_reached(cfg: PressureConfig) -> float:
        p = PressureState()
        step(
            p,
            delta=ant,
            traits=Traits(),
            relationship=Relationship(),
            dials=PersonaDials(),
            cfg=cfg,
            ts="2030-01-01T00:00:00+00:00",
        )
        return p.bars.joy

    cfg = PressureConfig()
    expected = cfg.anticipation_gain * a_v * a_w * cfg.idle_decay / cfg.anticipation_ref_decay
    assert floor_reached(cfg) == pytest.approx(expected, abs=1e-9)

    # The gain knob is read, not decorative.
    louder = PressureConfig(anticipation_gain=cfg.anticipation_gain * 2)
    assert floor_reached(louder) == pytest.approx(expected * 2, abs=1e-9)

    # Setting ref = idle_decay pins the floor absolute under decay retuning.
    fast = PressureConfig(idle_decay=0.036, anticipation_ref_decay=0.036)
    assert floor_reached(fast) == pytest.approx(fast.anticipation_gain * a_v * a_w, abs=1e-9)


# --------------------------------------------------------------------------- #
# 4. Canon on a platform without flock: still works, but says so once.         #
# --------------------------------------------------------------------------- #
def test_canon_warns_once_without_fcntl(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(canon_mod, "_fcntl", None)
    monkeypatch.setattr(canon_mod, "_no_flock_warned", False)
    c = Canon(tmp_path / "canon.jsonl")
    with caplog.at_level(logging.WARNING, logger=canon_mod.__name__):
        c.add("ava", "likes tea", action="likes")
        c.add("ava", "green tea best", action="prefers")
    warnings = [r for r in caplog.records if "cross-process file locking" in r.message]
    assert len(warnings) == 1  # loud exactly once, then quiet
    assert c.view()  # and the store still works on the in-process locks


# --------------------------------------------------------------------------- #
# 5. render()/inject() accept an injected clock, mirroring tick().             #
# --------------------------------------------------------------------------- #
def test_render_accepts_injected_now(tmp_path):
    eng = Engine(source=KeywordSource(), state_path=str(tmp_path / "s.json"))
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    eng.tick([{"role": "user", "content": "hello"}], now=t0)

    later = t0 + timedelta(days=3)
    a = eng.render(now=later)
    b = eng.render(now=later)
    assert a == b  # deterministic under a fixed clock

    injected = eng.inject("back again", now=later)
    assert a in injected  # inject() threads the same clock through render()
