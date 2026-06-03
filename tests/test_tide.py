"""Tide — the rising/falling shape of mood from recent valence history, and the
two new render clauses (tide direction + mixed feeling)."""

from feltstate.affect.tide import compute_tide
from feltstate.config import DEFAULT_CONFIG
from feltstate.render import render_felt_block
from feltstate.state import AffectState

CFG = DEFAULT_CONFIG.mood


def _hist(vals):
    return [{"valence": v, "arousal": 0.4, "labels": []} for v in vals]


def test_too_short_history_is_none():
    assert compute_tide(_hist([0.1, 0.2]), CFG) is None


def test_rising_trajectory():
    t = compute_tide(_hist([0.0, 0.1, 0.2, 0.4]), CFG)
    assert t and t["stage"] == "rising"


def test_falling_trajectory():
    t = compute_tide(_hist([0.5, 0.3, 0.1, -0.2]), CFG)
    assert t and t["stage"] == "falling"


def test_flat_and_neutral_is_none():
    assert compute_tide(_hist([0.02, 0.0, -0.02, 0.01]), CFG) is None


def test_held_high_reads_as_peak():
    t = compute_tide(_hist([0.5, 0.5, 0.5, 0.5]), CFG)
    assert t and t["stage"] == "peak"


def test_held_low_reads_as_valley():
    t = compute_tide(_hist([-0.5, -0.5, -0.5, -0.5]), CFG)
    assert t and t["stage"] == "valley"


def test_tide_is_rendered_on_the_mood_line():
    s = AffectState()
    s.mood.tide = {"stage": "rising", "intensity": 0.5}
    assert "lifting" in render_felt_block(s)
    s.mood.tide = {"stage": "falling", "intensity": 0.5}
    assert "sinking" in render_felt_block(s)


def test_mixed_feeling_is_rendered():
    s = AffectState()
    s.mood.mixed_blend = {"primary": "relieved", "secondary": "sad"}
    assert "relieved tinged with sad" in render_felt_block(s)


def test_no_tide_or_mixed_renders_clean_mood_line():
    # A fresh state has neither — the mood line must not gain stray clauses
    # (cache-stability: the common case stays byte-identical).
    out = render_felt_block(AffectState())
    assert "tinged with" not in out
    assert " · lifting" not in out and " · sinking" not in out
