"""Tests for feltstate.render.agent — the agent-scale one-line feeling readout.

Pinned behaviours:
* bands map mid-layer pressure onto the exp1-calibrated stuck grades;
* the text carries emotion words only — no task-cognition vocabulary
  ("stuck", "progress", "attempts"), the de-priming property verified in the
  affective-recovery series (exp7b);
* the line is a state description, never an instruction;
* labels come from the smoothed mood and the line stays band-stable under
  small drift (cache friendliness).
"""

from __future__ import annotations

import re

from feltstate import render_agent_feeling
from feltstate.render.agent import AGENT_BANDS
from feltstate.state import AffectState

# Words that would leak the conclusion into the prompt; the agent must reach
# "stuck" on its own (exp7b's de-priming guarantee).
COGNITION_WORDS = re.compile(r"stuck|progress|attempt|landing|moving|task|step|fail", re.IGNORECASE)


def state_with(mid: float, labels: list[str] | None = None) -> AffectState:
    s = AffectState()
    s.pressure.bars.anxiety = mid
    s.mood.labels = labels or []
    return s


# The band each level must render, written out rather than recomputed. The
# previous version re-ran render_agent_feeling's own band search inside the
# assertion, so it agreed with whatever the implementation did: flipping the
# production comparison from >= to > passed unnoticed. A table states the
# expectation independently, and the boundary values are the ones that catch
# an off-by-one.
EXPECTED_BANDS: tuple[tuple[float, str], ...] = (
    (0.00, "steady and settled"),
    (0.05, "steady and settled"),
    (0.10, "slightly uneasy"),  # exactly on a bound: inclusive
    (0.12, "slightly uneasy"),
    (0.17, "restless and frustrated — noticeably, more than a moment ago"),
    (0.20, "restless and frustrated — noticeably, more than a moment ago"),
    (0.42, "very frustrated and tense — strongly, and it has been building for a while"),
    (0.50, "very frustrated and tense — strongly, and it has been building for a while"),
    (0.70, "worn down and tense — heavily, and it has kept building"),
    (0.80, "worn down and tense — heavily, and it has kept building"),
)


def test_bands_cover_all_grades():
    for mid, expected in EXPECTED_BANDS:
        line = render_agent_feeling(state_with(mid))
        assert expected in line, f"{mid} rendered {line!r}"
    # every band is reachable
    assert {phrase for _, phrase in EXPECTED_BANDS} == {phrase for _, phrase in AGENT_BANDS}


def test_no_cognition_vocabulary_in_any_band():
    for _, phrase in AGENT_BANDS:
        assert not COGNITION_WORDS.search(phrase), phrase
    # and the rendered line itself stays clean
    line = render_agent_feeling(state_with(0.5, ["frustrated", "tense"]))
    assert not COGNITION_WORDS.search(line)


def test_labels_are_shown_and_default_to_even():
    assert "frustrated, anxious" in render_agent_feeling(state_with(0.3, ["frustrated", "anxious"]))
    assert "even" in render_agent_feeling(state_with(0.0))


def test_band_stability_under_small_drift():
    a = render_agent_feeling(state_with(0.250))
    b = render_agent_feeling(state_with(0.262))  # same band -> same phrase
    assert a == b


def test_is_description_not_instruction():
    line = render_agent_feeling(state_with(0.8, ["tense"]))
    for verb in ("you should", "you must", "say ", "report", "stop", "change"):
        assert verb not in line.lower()
