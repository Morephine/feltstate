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
COGNITION_WORDS = re.compile(
    r"stuck|progress|attempt|landing|moving|task|step|fail", re.IGNORECASE
)


def state_with(mid: float, labels: list[str] | None = None) -> AffectState:
    s = AffectState()
    s.pressure.bars.anxiety = mid
    s.mood.labels = labels or []
    return s


def test_bands_cover_all_grades():
    seen = set()
    for mid in (0.0, 0.12, 0.20, 0.50, 0.80):
        line = render_agent_feeling(state_with(mid))
        for bound, phrase in AGENT_BANDS:
            if mid >= bound:
                assert phrase in line
                seen.add(phrase)
                break
    assert len(seen) == len(AGENT_BANDS)  # every band reachable


def test_no_cognition_vocabulary_in_any_band():
    for _, phrase in AGENT_BANDS:
        assert not COGNITION_WORDS.search(phrase), phrase
    # and the rendered line itself stays clean
    line = render_agent_feeling(state_with(0.5, ["frustrated", "tense"]))
    assert not COGNITION_WORDS.search(line)


def test_labels_are_shown_and_default_to_even():
    assert "frustrated, anxious" in render_agent_feeling(
        state_with(0.3, ["frustrated", "anxious"]))
    assert "even" in render_agent_feeling(state_with(0.0))


def test_band_stability_under_small_drift():
    a = render_agent_feeling(state_with(0.250))
    b = render_agent_feeling(state_with(0.262))  # same band -> same phrase
    assert a == b


def test_is_description_not_instruction():
    line = render_agent_feeling(state_with(0.8, ["tense"]))
    for verb in ("you should", "you must", "say ", "report", "stop", "change"):
        assert verb not in line.lower()
