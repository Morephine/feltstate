"""feltstate.companion.express — derive an expression signal from a state change.

A pure helper the loop calls after each tick. It detects a pressure-release
*transition* — the edge into the ``"releasing"`` phase — and returns the release
flavour to express; otherwise it falls back to the dominant mood label. The
result is a feltstate label string for a :class:`FrontendAdapter` to map.

This is a *derivation*, never a command: it reads state the engine already
produced and is never wired into the engine itself (tool, not controller).
"""

from __future__ import annotations

from ..state import AffectState


def expression_signal(prev: AffectState | None, new: AffectState) -> str | None:
    """Return the label to express given the state before/after a tick.

    Edge-triggered: when ``new`` has *just* entered the ``"releasing"`` phase
    (``prev`` was not releasing), return the pressure ``release_type`` —
    ``"tears"`` / ``"anger"`` / ``"anxious"`` / ``"withdraw"`` / ``"burst_joy"``
    / ``"collapse"``. Otherwise return the dominant mood label
    (``new.mood.labels[0]``), or ``None`` if there is none.

    ``prev`` may be ``None`` (first tick) — treated as a ``"calm"`` prior, so a
    state that starts already releasing still fires its release flavour once.
    """
    prev_phase = prev.pressure.phase if prev is not None else "calm"
    if prev_phase != "releasing" and new.pressure.phase == "releasing":
        if new.pressure.release_type:
            return new.pressure.release_type
    labels = new.mood.labels
    return labels[0] if labels else None
