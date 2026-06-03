"""Tests for feltstate.state — the shared schema layer.

These check the one thing the dynamics layers all rely on: an
:class:`AffectState` survives a full ``to_dict`` / ``from_dict`` round trip and a
``save`` / ``load`` to disk without losing or mangling any field. Numbers are
rounded on serialization (by design), so the assertions allow for that.
"""

from __future__ import annotations

from feltstate import (
    AffectDelta,
    AffectState,
    Mood,
    PressureBars,
    PressureState,
    Relationship,
    Traits,
)
from feltstate.state import BAR_NAMES


def _rich_state() -> AffectState:
    """Build a fully-populated state with every optional field set, so a
    round trip has something to lose if it drops a field."""
    return AffectState(
        mood=Mood(
            valence=0.42,
            arousal=0.61,
            labels=["content", "curious"],
            aftertaste={"valence": 0.3, "arousal": 0.5, "weight": 0.5},
        ),
        traits=Traits(depression=0.34, optimism=0.71, anxiety=0.28, curiosity=0.66),
        relationship=Relationship(
            closeness=0.72,
            trust=0.64,
            safety=0.58,
            unresolved_tension=0.22,
            repair_history=0.4,
        ),
        pressure=PressureState(
            bars=PressureBars(sadness=0.12, anger=0.05, anxiety=0.3, boundary=0.0, joy=0.61),
            phase="building",
            release_type="burst_joy",
            release_secondary="tears",
            release_started_ts="2020-01-01T12:00:00+00:00",
            release_ends_ts="2020-01-01T12:05:00+00:00",
            aftertaste_until_ts="2020-01-01T12:20:00+00:00",
            last_tick_ts="2020-01-01T12:00:00+00:00",
            history=[{"ts": "2020-01-01T12:00:00+00:00", "release_type": "burst_joy"}],
        ),
        last_tick_ts="2020-01-01T12:00:00+00:00",
        history=[
            {
                "ts": "2020-01-01T12:00:00+00:00",
                "valence": 0.4,
                "arousal": 0.6,
                "labels": ["content"],
            }
        ],
    )


def test_affect_delta_round_trip():
    d = AffectDelta(
        valence=-0.3,
        arousal=0.7,
        labels=["sad", "tired"],
        confidence=0.55,
        monologue="a quiet ache",
        anticipation={"valence": 0.4, "arousal": 0.3, "weight": 0.6},
        mixed_blend={
            "primary": "sad",
            "secondary": "hopeful",
            "primary_score": 0.6,
            "secondary_score": 0.3,
        },
        milestones=[{"kind": "care", "actor": "user", "severity": 0.5}],
    )
    back = AffectDelta.from_dict(d.to_dict())
    assert back.valence == d.valence
    assert back.arousal == d.arousal
    assert back.labels == d.labels
    assert back.confidence == d.confidence
    assert back.monologue == d.monologue
    assert back.anticipation == d.anticipation
    assert back.mixed_blend == d.mixed_blend
    assert back.milestones == d.milestones


def test_state_dict_round_trip_preserves_every_field():
    state = _rich_state()
    back = AffectState.from_dict(state.to_dict())

    # mood
    assert back.mood.valence == state.mood.valence
    assert back.mood.arousal == state.mood.arousal
    assert back.mood.labels == state.mood.labels
    assert back.mood.aftertaste == state.mood.aftertaste

    # traits — all four dimensions
    assert back.traits.to_dict() == state.traits.to_dict()

    # relationship — all five fields
    assert back.relationship.to_dict() == state.relationship.to_dict()

    # pressure scalars and timing windows
    assert back.pressure.phase == state.pressure.phase
    assert back.pressure.release_type == state.pressure.release_type
    assert back.pressure.release_secondary == state.pressure.release_secondary
    assert back.pressure.release_started_ts == state.pressure.release_started_ts
    assert back.pressure.release_ends_ts == state.pressure.release_ends_ts
    assert back.pressure.aftertaste_until_ts == state.pressure.aftertaste_until_ts

    # pressure bars — every named bar survives
    for name in BAR_NAMES:
        assert getattr(back.pressure.bars, name) == getattr(state.pressure.bars, name)

    # top-level bookkeeping
    assert back.last_tick_ts == state.last_tick_ts
    assert back.history == state.history
    assert back.pressure.history == state.pressure.history


def test_state_save_load_disk_round_trip(tmp_path):
    state = _rich_state()
    path = tmp_path / "nested" / "state.json"  # parent dir should be auto-created
    state.save(path)
    assert path.is_file()

    loaded = AffectState.load(path)
    # Whole-state dict equality is the strongest "no field lost" assertion.
    assert loaded.to_dict() == state.to_dict()


def test_load_missing_file_returns_default_state(tmp_path):
    missing = tmp_path / "does_not_exist.json"
    state = AffectState.load(missing)
    # A fresh default state, not an error.
    assert isinstance(state, AffectState)
    assert state.to_dict() == AffectState().to_dict()


def test_load_corrupt_file_returns_default_state(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{ this is not json", encoding="utf-8")
    state = AffectState.load(path)
    assert state.to_dict() == AffectState().to_dict()


def test_from_dict_tolerates_empty_and_partial_input():
    # Empty dict -> all defaults.
    assert AffectState.from_dict({}).to_dict() == AffectState().to_dict()
    # Partial dict -> only the supplied sub-tree changes, the rest defaults.
    partial = AffectState.from_dict({"traits": {"optimism": 0.8}})
    assert partial.traits.optimism == 0.8
    assert partial.traits.depression == 0.5  # default preserved
    assert partial.mood.to_dict() == Mood().to_dict()


def test_history_is_capped_on_serialization():
    # to_dict keeps only the last 50 readings; a longer history is trimmed.
    long_history = [
        {"ts": str(i), "valence": 0.0, "arousal": 0.4, "labels": []} for i in range(120)
    ]
    state = AffectState(history=long_history)
    assert len(state.to_dict()["history"]) == 50
    # The kept slice is the most recent tail.
    assert state.to_dict()["history"][-1]["ts"] == "119"
