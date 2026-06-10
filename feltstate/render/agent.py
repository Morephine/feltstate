"""feltstate.render.agent — a one-line feeling readout for working agents.

:func:`render_felt_block` (the companion renderer) speaks at companion scale:
its phrase bands assume the pressure cooker's 0.85 release threshold, and it
includes a relationship line — both wrong for a task agent ticked once per
tool step. This module is the agent-scale counterpart, validated in the
affective-recovery experiment series (exp7/exp7b):

* bands follow the agent-scale stuck grades measured in exp1 with
  :func:`~feltstate.config.agent_scale_config` (mid-layer peak ~0.17 after a
  ~4-step stall, ~0.42 after ~10, ~0.70 entering the cooker's "building"
  phase after a long spiral);
* the text carries **emotion words and intensity only** — no task-cognition
  vocabulary ("stuck", "no progress", "attempts"). De-primed phrasing was
  verified to matter: given only emotion words, the agent still concluded and
  *said* it was stuck, naming the stuck object itself, and never echoed the
  line on healthy runs (exp7b: 3/3 reports, 0/3 false alarms).

Same design rules as the companion renderer: first-person state, never an
instruction (the agent decides what to do about it), and discrete phrase
bands so small tick-to-tick drift does not break prompt caching.

Usage::

    from feltstate import Engine, agent_scale_config
    from feltstate.render.agent import render_agent_feeling

    engine = Engine(source=..., config=agent_scale_config(), ...)
    state = engine.tick([...])
    line = render_agent_feeling(state)
    # -> "[how you feel: frustrated, anxious | restless and frustrated —
    #     noticeably, more than a moment ago]"
    # append `line` after the tool result in the next user message
"""

from __future__ import annotations

from ..state import AffectState

__all__ = ["render_agent_feeling", "AGENT_BANDS"]

# (lower bound, phrase) — bands match exp1's measured stuck grades under
# agent_scale_config. Order matters: first band whose bound fits wins.
AGENT_BANDS: tuple[tuple[float, str], ...] = (
    (0.70, "worn down and tense — heavily, and it has kept building"),
    (0.42, "very frustrated and tense — strongly, and it has been building for a while"),
    (0.17, "restless and frustrated — noticeably, more than a moment ago"),
    (0.10, "slightly uneasy"),
    (0.00, "steady and settled"),
)


def render_agent_feeling(
    state: AffectState,
    *,
    header: str = "how you feel",
    bands: tuple[tuple[float, str], ...] = AGENT_BANDS,
) -> str:
    """Render ``state`` as a one-line agent-scale feeling readout.

    The line is ``[<header>: <mood labels> | <band phrase>]``. Labels come from
    the smoothed fast mood (so they do not flicker); the band phrase tracks the
    highest pressure bar — at agent scale that bar is the validated
    accumulating "how long has this felt off" signal.

    State description only, never an instruction: the agent reads it as its
    own feeling and decides for itself whether to say or change anything.
    """
    bars = state.pressure.bars.to_dict()
    mid = max(bars.values()) if bars else 0.0
    labels = ", ".join(state.mood.labels or []) or "even"
    phrase = bands[-1][1]
    for bound, text in bands:
        if mid >= bound:
            phrase = text
            break
    return f"[{header}: {labels} | {phrase}]"
