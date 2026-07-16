"""Tests for feltstate.state — the shared schema layer.

These check the one thing the dynamics layers all rely on: an
:class:`AffectState` survives a full ``to_dict`` / ``from_dict`` round trip and a
``save`` / ``load`` to disk without losing or mangling any field. Numbers are
rounded on serialization (by design), so the assertions allow for that.
"""

from __future__ import annotations

import pytest

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


def test_load_corrupt_file_quarantines_and_warns(tmp_path):
    # A corrupt state file must not be silently reset (that would wipe an agent's
    # whole temperament with no trace). It is quarantined aside and a loud warning
    # is emitted; only then does a fresh default boot.
    path = tmp_path / "broken.json"
    path.write_text("{ this is not json", encoding="utf-8")

    with pytest.warns(UserWarning, match="corrupt"):
        state = AffectState.load(path)

    # Still boots a fresh default so the agent can run...
    assert state.to_dict() == AffectState().to_dict()
    # ...but the corrupt bytes are preserved (renamed aside), never lost, and the
    # original path no longer holds the bad file.
    assert not path.exists()
    quarantined = list(tmp_path.glob("broken.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "{ this is not json"


def test_load_semantically_corrupt_file_is_quarantined(tmp_path):
    # Valid JSON but garbage values (a non-numeric felt scalar) is still corrupt:
    # it must be quarantined, not crash the boot and not silently reset.
    path = tmp_path / "garbage.json"
    path.write_text('{"mood": {"valence": "not-a-number"}}', encoding="utf-8")

    with pytest.warns(UserWarning, match="corrupt"):
        state = AffectState.load(path)

    assert state.to_dict() == AffectState().to_dict()
    assert not path.exists()
    assert len(list(tmp_path.glob("garbage.json.corrupt-*"))) == 1


def test_load_non_object_json_root_is_quarantined(tmp_path):
    path = tmp_path / "list-root.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.warns(UserWarning, match="corrupt"):
        state = AffectState.load(path)

    assert state.to_dict() == AffectState().to_dict()
    assert not path.exists()
    quarantined = list(tmp_path.glob("list-root.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "[]"


def test_load_valid_file_is_not_quarantined(tmp_path):
    # Guard the other direction: a good state file loads intact, is left in place,
    # and emits no warning (the quarantine path must not over-fire).
    import warnings

    state = _rich_state()
    path = tmp_path / "good.json"
    state.save(path)

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any warning would fail the test
        loaded = AffectState.load(path)

    assert loaded.to_dict() == state.to_dict()
    assert path.is_file()  # original untouched
    assert list(tmp_path.glob("good.json.corrupt-*")) == []


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


# --------------------------------------------------------------------------- #
# AffectDelta — NaN/Inf sanitisation boundary                                #
# --------------------------------------------------------------------------- #
def test_affect_delta_rejects_nan_valence():
    """A float('nan') reading must not propagate — _finite guards the seam."""
    d = AffectDelta(valence=float("nan"), arousal=float("nan"), confidence=float("nan"))
    assert d.valence == 0.0  # NaN falls back to the valence default
    assert d.arousal == 0.4  # NaN falls back to the arousal default
    assert d.confidence == 0.7  # NaN falls back to the confidence default


def test_affect_delta_rejects_inf_values():
    """Positive and negative infinity must also be rejected (clamping propagates them)."""
    d = AffectDelta(valence=float("inf"), arousal=float("-inf"))
    assert d.valence == 0.0
    assert d.arousal == 0.4


def test_affect_delta_from_dict_sanitises_non_finite():
    """from_dict arrives from an external source and must apply the same guard."""
    back = AffectDelta.from_dict(
        {"valence": float("nan"), "arousal": float("inf"), "confidence": float("-inf")}
    )
    assert back.valence == 0.0
    assert back.arousal == 0.4
    assert back.confidence == 0.7


def test_affect_delta_from_dict_sanitises_string_nan():
    """A source that returns the string 'NaN' must not be treated as a real emotion."""
    back = AffectDelta.from_dict({"valence": "NaN", "arousal": "Infinity"})
    assert back.valence == 0.0
    assert back.arousal == 0.4


# --------------------------------------------------------------------------- #
# PressureBars — max_bar and at_or_above helpers                             #
# --------------------------------------------------------------------------- #
def test_pressure_bars_max_bar_returns_highest():
    bars = PressureBars(sadness=0.1, anger=0.7, anxiety=0.5, boundary=0.2, joy=0.3)
    name, val = bars.max_bar()
    assert name == "anger"
    assert val == 0.7


def test_pressure_bars_max_bar_with_tie_is_deterministic():
    """Ties are broken by BAR_NAMES order via max() — the exact winner is less
    important than the fact that the call never raises and always returns one bar."""
    bars = PressureBars(sadness=0.9, anger=0.9, anxiety=0.0, boundary=0.0, joy=0.0)
    name, val = bars.max_bar()
    assert val == 0.9
    assert name in ("sadness", "anger")


def test_pressure_bars_at_or_above_empty_when_all_below():
    bars = PressureBars(sadness=0.3, anger=0.2, anxiety=0.1, boundary=0.0, joy=0.5)
    assert bars.at_or_above(0.9) == []


def test_pressure_bars_at_or_above_respects_threshold_and_ordering():
    bars = PressureBars(sadness=0.4, anger=0.85, anxiety=0.90, boundary=0.1, joy=0.87)
    above = bars.at_or_above(0.85)
    names = [n for n, _ in above]
    vals = [v for _, v in above]
    # Three bars crossed (anger=0.85, anxiety=0.90, joy=0.87).
    assert set(names) == {"anger", "anxiety", "joy"}
    # Sorted highest-first.
    assert vals == sorted(vals, reverse=True)


def test_pressure_bars_at_or_above_includes_exact_threshold():
    """A bar sitting exactly on the threshold must be included (at_or_above)."""
    bars = PressureBars(sadness=0.85)
    above = bars.at_or_above(0.85)
    assert len(above) == 1 and above[0][0] == "sadness"


# --------------------------------------------------------------------------- #
# PressureState — history is capped at 5 on serialization                    #
# --------------------------------------------------------------------------- #
def test_pressure_history_is_capped_to_five_on_serialization():
    """PressureState.to_dict() retains at most the 5 most recent release events."""
    events = [{"ts": str(i), "release_type": "burst_joy"} for i in range(20)]
    ps = PressureState(history=events)
    d = ps.to_dict()
    assert len(d["history"]) == 5
    # The tail (most recent) is kept, not the head.
    assert d["history"][-1]["ts"] == "19"


def test_pressure_state_from_dict_also_caps_history():
    """from_dict honours the same 5-event cap (a stale file with more events is safe)."""
    events = [{"ts": str(i), "release_type": "tears"} for i in range(30)]
    ps = PressureState.from_dict({"history": events})
    assert len(ps.history) == 5
    assert ps.history[-1]["ts"] == "29"


# --------------------------------------------------------------------------- #
# Traits — baseline field tolerates non-dict values in stored JSON            #
# --------------------------------------------------------------------------- #
def test_traits_from_dict_with_null_baseline_defaults_to_empty():
    """A state file where 'baseline' is null or absent must load without error
    and default to an empty dict (neutral resting point for all traits)."""
    t = Traits.from_dict({"optimism": 0.7, "baseline": None})
    assert t.baseline == {}
    assert t.optimism == 0.7


def test_traits_from_dict_with_list_baseline_falls_back_to_empty():
    """A baseline stored as a list (e.g. a serialisation glitch) must be
    silently ignored rather than crashing — the guard treats non-dicts as empty."""
    t = Traits.from_dict({"baseline": ["not", "a", "dict"]})
    assert t.baseline == {}


def test_traits_from_dict_baseline_filters_unknown_keys():
    """Only the four _TRAIT_KEYS may appear in the loaded baseline; unknown
    fields (from a future schema extension) are discarded, not carried through."""
    t = Traits.from_dict({"baseline": {"optimism": 0.6, "unknown_future_trait": 0.9}})
    assert "optimism" in t.baseline
    assert "unknown_future_trait" not in t.baseline


# --------------------------------------------------------------------------- #
# AffectDelta.from_dict — tolerates None and unknown fields                   #
# --------------------------------------------------------------------------- #
def test_affect_delta_from_dict_none_gives_defaults():
    """from_dict(None) must produce the same result as the no-arg constructor."""
    assert AffectDelta.from_dict(None).to_dict() == AffectDelta().to_dict()


def test_affect_delta_from_dict_ignores_unknown_fields():
    """Extra keys in a future schema version must not cause from_dict to fail."""
    back = AffectDelta.from_dict({"valence": 0.3, "future_field": "ignored"})
    assert back.valence == 0.3
