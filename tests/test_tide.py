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


# --- edge-case / invariant tests ------------------------------------------


def test_exactly_three_readings_is_enough():
    # The minimum valid history is 3 items; 2 returns None.
    assert compute_tide(_hist([0.0, 0.0, 0.0]), CFG) is None  # flat+neutral, still None
    t = compute_tide(_hist([0.0, 0.0, 0.6]), CFG)
    assert t is not None and t["stage"] == "rising"


def test_intensity_is_clamped_at_one_for_large_swings():
    # A swing far beyond _FULL_SWING (0.5) must saturate at 1.0, never exceed it.
    t = compute_tide(_hist([-0.9, -0.9, -0.9, 0.9]), CFG)
    assert t is not None
    assert t["intensity"] <= 1.0
    assert t["stage"] == "rising"


def test_intensity_is_always_positive():
    # Intensity must be in [0, 1] for all valid (non-None) return values.
    cases = [
        _hist([0.6, 0.6, 0.6, 0.6]),  # peak
        _hist([-0.6, -0.6, -0.6, -0.6]),  # valley
        _hist([0.0, 0.0, 0.0, 0.4]),  # rising
        _hist([0.4, 0.4, 0.4, 0.0]),  # falling
    ]
    for h in cases:
        t = compute_tide(h, CFG)
        if t is not None:
            assert 0.0 <= t["intensity"] <= 1.0, f"intensity out of range: {t}"


def test_rising_takes_precedence_over_peak():
    # A valence climbing *through* extreme territory is "rising", not "peak".
    # recent = 0.8 (>> _EXTREME=0.35), but swing is also large positive.
    t = compute_tide(_hist([0.0, 0.1, 0.2, 0.8]), CFG)
    assert t is not None and t["stage"] == "rising"


def test_falling_takes_precedence_over_valley():
    # A valence sliding *through* deeply negative territory is "falling", not "valley".
    t = compute_tide(_hist([0.0, -0.1, -0.2, -0.8]), CFG)
    assert t is not None and t["stage"] == "falling"


def test_non_dict_entries_in_history_are_skipped():
    # Corrupted / heterogeneous history (with None or non-dicts) must not crash.
    mixed = [None, "garbage", {"valence": 0.0}, {"valence": 0.0}, {"valence": 0.5}]
    t = compute_tide(mixed, CFG)
    # Only the three dicts survive; recent=0.5, earlier mean=0.0 -> rising.
    assert t is not None and t["stage"] == "rising"


def test_empty_history_is_none():
    assert compute_tide([], CFG) is None


def test_single_reading_is_none():
    assert compute_tide(_hist([0.9]), CFG) is None


def test_result_stage_is_always_a_known_value():
    # Every non-None result must carry one of the four known stage strings.
    valid_stages = {"rising", "falling", "peak", "valley"}
    histories = [
        _hist([0.0, 0.1, 0.4]),
        _hist([0.4, 0.1, -0.1]),
        _hist([0.5, 0.5, 0.5]),
        _hist([-0.5, -0.5, -0.5]),
    ]
    for h in histories:
        t = compute_tide(h, CFG)
        if t is not None:
            assert t["stage"] in valid_stages, f"unexpected stage: {t['stage']}"


def test_intensity_rounded_to_three_decimal_places():
    # The contract says intensity is rounded to 3 dp — check it never has more.
    t = compute_tide(_hist([0.0, 0.0, 0.0, 0.4]), CFG)
    assert t is not None
    assert t["intensity"] == round(t["intensity"], 3)


def test_window_truncation_uses_only_last_n_readings():
    # tide_window=5 by default. Older readings beyond the window are ignored.
    # If they were included the mean would be pulled positive, changing the stage.
    # We use -0.5 (< -_EXTREME=0.35) so the 5-reading window returns "valley".
    old_noise = [{"valence": 0.9}] * 20
    recent = _hist([-0.5, -0.5, -0.5, -0.5, -0.5])
    h = old_noise + recent
    t = compute_tide(h, CFG)
    # All 5 in-window readings are flat at -0.5, swing=0 but recent <= -_EXTREME -> valley.
    assert t is not None and t["stage"] == "valley"
