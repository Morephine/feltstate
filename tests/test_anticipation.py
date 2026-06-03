"""Anticipation scheduling — a looked-forward-to event holds a joy floor that
ramps up as the event nears (dopamine pre-payment), or stays flat with no schedule."""

from feltstate.affect.pressure import _anticipation_progress, step
from feltstate.config import DEFAULT_CONFIG, PersonaDials
from feltstate.state import AffectDelta, PressureState, Relationship, Traits

PCFG = DEFAULT_CONFIG.pressure


def test_no_schedule_is_full_floor():
    assert _anticipation_progress({"valence": 0.8, "weight": 0.5}, "2026-06-03T12:00:00") == 1.0


def test_progress_ramps_linearly_toward_the_event():
    ant = {
        "valence": 0.8,
        "weight": 0.5,
        "since_ts": "2026-06-03T00:00:00",
        "event_ts": "2026-06-03T10:00:00",
    }
    early = _anticipation_progress(ant, "2026-06-03T01:00:00")  # ~10% of the way
    late = _anticipation_progress(ant, "2026-06-03T09:00:00")  # ~90% of the way
    assert 0.0 <= early < late <= 1.0
    assert abs(early - 0.1) < 0.02 and abs(late - 0.9) < 0.02


def test_bad_schedule_falls_back_to_full():
    assert _anticipation_progress({"event_ts": "not-a-date"}, "2026-06-03T01:00:00") == 1.0


def _joy_at(progress_ts: str) -> float:
    p = PressureState()
    d = AffectDelta(
        anticipation={
            "valence": 1.0,
            "weight": 1.0,
            "since_ts": "2026-06-03T00:00:00",
            "event_ts": "2026-06-03T10:00:00",
        }
    )
    step(
        p,
        delta=d,
        traits=Traits(),
        relationship=Relationship(),
        dials=PersonaDials(),
        cfg=PCFG,
        ts=progress_ts,
    )
    return p.bars.joy


def test_scheduled_anticipation_builds_joy_toward_the_event():
    early = _joy_at("2026-06-03T01:00:00")
    late = _joy_at("2026-06-03T09:00:00")
    assert late > early  # the joy floor sits higher nearer the event
