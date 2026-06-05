"""Tests for the expression-signal derivation (companion.express)."""

from __future__ import annotations

from feltstate import AffectState, Mood, PressureState
from feltstate.companion import expression_signal


def _state(phase: str = "calm", release_type: str | None = None, labels=None) -> AffectState:
    return AffectState(
        mood=Mood(labels=labels or []),
        pressure=PressureState(phase=phase, release_type=release_type),
    )


def test_release_edge_returns_release_type():
    prev = _state(phase="building", labels=["sad"])
    new = _state(phase="releasing", release_type="tears", labels=["sad"])
    assert expression_signal(prev, new) == "tears"


def test_no_transition_falls_back_to_mood_label():
    prev = _state(phase="calm", labels=["content"])
    new = _state(phase="calm", labels=["joyful"])
    assert expression_signal(prev, new) == "joyful"


def test_already_releasing_is_not_a_new_edge():
    # Both ticks are in 'releasing' -> not a fresh entry -> fall back to mood.
    prev = _state(phase="releasing", release_type="tears", labels=["sad"])
    new = _state(phase="releasing", release_type="tears", labels=["sad"])
    assert expression_signal(prev, new) == "sad"


def test_none_prev_treated_as_calm():
    new = _state(phase="releasing", release_type="burst_joy", labels=["joyful"])
    assert expression_signal(None, new) == "burst_joy"


def test_releasing_without_type_falls_back_to_label():
    new = _state(phase="releasing", release_type=None, labels=["tender"])
    assert expression_signal(_state(phase="calm"), new) == "tender"


def test_no_labels_returns_none():
    assert expression_signal(None, _state()) is None
