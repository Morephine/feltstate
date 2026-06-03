"""Tests for feltstate.render — identity-merge felt block + cache-safe injection.

Two properties matter here:

* **Words, not numbers.** ``render_felt_block`` emits discrete first-person
  phrase bands, never raw values — so the agent reads a feeling, not a dashboard.
* **Cache-stability.** Because the bands are coarse, two adjacent ticks whose
  numbers drift only slightly render to a *byte-identical* block. That is what
  keeps the per-turn injection from busting the prompt cache.

And for injection: the felt block must ride at the end (in the user turn), with
the user's words after it, never spliced into a static system prompt.
"""
from __future__ import annotations

import re

from feltstate import (
    AffectState,
    Mood,
    Traits,
    Relationship,
    PressureState,
    PressureBars,
    PersonaDials,
    render_felt_block,
    build_injection,
)


def _state(**over) -> AffectState:
    """A mid-range state; override individual sub-objects via kwargs."""
    base = dict(
        mood=Mood(valence=0.25, arousal=0.5, labels=["content", "curious"]),
        traits=Traits(depression=0.5, optimism=0.5, anxiety=0.5, curiosity=0.5),
        # Mid closeness (< 0.70): a settled, neutral footing that on its own
        # emits no tone line, so the persona-dial tests can isolate the dials.
        relationship=Relationship(closeness=0.55, trust=0.55, safety=0.55),
        pressure=PressureState(bars=PressureBars(joy=0.3)),
    )
    base.update(over)
    return AffectState(**base)


# --------------------------------------------------------------------------- #
# Structure / header                                                          #
# --------------------------------------------------------------------------- #
def test_block_contains_header_and_core_lines():
    block = render_felt_block(_state())
    assert block.startswith("[how I feel right now]")
    # One line per core dimension, by their first-person prefixes.
    assert "with you:" in block
    assert "mood:" in block
    assert "inside:" in block
    assert "underneath:" in block


def test_custom_header_is_used():
    block = render_felt_block(_state(), header="[my inner weather]")
    assert block.startswith("[my inner weather]")


def test_time_line_inserted_right_after_header_when_present():
    block = render_felt_block(_state(), time_line="Wed morning 8:11")
    lines = block.splitlines()
    assert lines[0] == "[how I feel right now]"
    assert lines[1] == "Wed morning 8:11"


def test_empty_time_line_adds_no_extra_line():
    with_line = render_felt_block(_state(), time_line="")
    no_arg = render_felt_block(_state())
    assert with_line == no_arg  # empty time_line changes nothing


# --------------------------------------------------------------------------- #
# Words, not numbers                                                          #
# --------------------------------------------------------------------------- #
def test_block_uses_words_not_raw_numbers():
    block = render_felt_block(
        _state(
            relationship=Relationship(closeness=0.723, trust=0.641, safety=0.587),
            mood=Mood(valence=0.314, arousal=0.659, labels=["content"]),
        )
    )
    # No decimal numbers leak into the rendered feeling (the clock-style "8:11"
    # is only added via time_line, which we don't pass here).
    assert not re.search(r"\d+\.\d+", block), f"raw number leaked: {block!r}"
    # And the phrase bands themselves show up as words.
    assert "close" in block            # closeness 0.72 band
    assert "lightly lifted" in block   # valence ~0.31 band


# --------------------------------------------------------------------------- #
# Cache-stability: small numeric drift -> identical block                     #
# --------------------------------------------------------------------------- #
def test_small_drift_renders_byte_identical():
    """Two states whose numbers differ slightly but stay inside the same bands
    must render to exactly the same string (so injecting it every turn keeps the
    prompt cache warm)."""
    s1 = _state(
        relationship=Relationship(closeness=0.71, trust=0.71, safety=0.71),
        mood=Mood(valence=0.25, arousal=0.50, labels=["content", "curious"]),
        traits=Traits(depression=0.50, optimism=0.50, anxiety=0.50, curiosity=0.50),
        pressure=PressureState(bars=PressureBars(joy=0.30)),
    )
    s2 = _state(
        relationship=Relationship(closeness=0.74, trust=0.74, safety=0.74),   # still "close"/"trusted"/"safe"
        mood=Mood(valence=0.30, arousal=0.55, labels=["content", "curious"]),  # still same bands
        traits=Traits(depression=0.52, optimism=0.48, anxiety=0.51, curiosity=0.49),  # still "mid"
        pressure=PressureState(bars=PressureBars(joy=0.33)),                   # still "a flicker of joy"
    )
    assert render_felt_block(s1) == render_felt_block(s2)


def test_large_change_does_change_block():
    """Sanity check the opposite direction: a band-crossing change *does* alter
    the block (so the test above is meaningful, not vacuous)."""
    calm = _state(mood=Mood(valence=0.25, arousal=0.5, labels=["content"]))
    low = _state(mood=Mood(valence=-0.6, arousal=0.2, labels=["sad"]))
    assert render_felt_block(calm) != render_felt_block(low)


def test_neutral_dials_omit_tone_line_for_settled_state():
    """A neutral persona with a mid, settled state emits no 'how it lands' tone
    line — keeping the common case short and cache-stable."""
    block = render_felt_block(_state(), dials=PersonaDials())
    assert "how it lands:" not in block


def test_off_neutral_dials_add_a_tone_line():
    block = render_felt_block(
        _state(relationship=Relationship(closeness=0.75, trust=0.7, safety=0.7)),
        dials=PersonaDials(warmth=0.9, emotional_explicitness=0.9),
    )
    assert "how it lands:" in block


def test_releasing_phase_adds_a_right_now_texture_line():
    p = PressureState(
        bars=PressureBars(joy=0.9),
        phase="releasing",
        release_type="burst_joy",
    )
    block = render_felt_block(_state(pressure=p))
    assert "right now:" in block


# --------------------------------------------------------------------------- #
# build_injection: cache-safe placement                                       #
# --------------------------------------------------------------------------- #
def test_build_injection_places_block_before_user_message():
    felt = "[how I feel right now]\nmood: content | level, mild energy"
    user = "hey, how's it going?"
    out = build_injection(felt, user)
    # Felt block comes first (dynamic prefix to the turn), user words after it.
    assert out.startswith(felt)
    assert out.endswith(user)
    assert out.index(felt) < out.index(user)
    # Separated by a blank line.
    assert "\n\n" in out


def test_build_injection_empty_block_returns_user_message_unchanged():
    assert build_injection("", "just the message") == "just the message"
    assert build_injection("   \n  ", "just the message") == "just the message"


def test_build_injection_does_not_wrap_in_system_prompt():
    # Discipline check: the function returns the user-turn content, not a
    # system-prompt blob — nothing system-y is added around it.
    out = build_injection("felt", "msg")
    assert out == "felt\n\nmsg"
