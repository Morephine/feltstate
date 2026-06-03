"""feltstate.affect.tide — the rising/falling shape of mood over recent turns.

Mood is not just a level; it has a *direction*. Two agents both sitting at a
neutral valence feel different if one is climbing out of a low and the other is
sliding down from a high. ``tide`` reads that trajectory from the recent valence
history and names it — ``rising``, ``peak``, ``falling``, or ``valley`` — so the
felt block can say "lifting" or "sinking", not only "level".

This is a pure read-only derivation: it inspects the rolling history and returns
a small ``{"stage", "intensity"}`` dict (or ``None`` when the mood is flat and
unremarkable). It never changes the mood. The engine computes it once per tick
and stores it on :attr:`feltstate.state.Mood.tide`.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..config import MoodConfig

# A mood sitting steadily at or beyond this magnitude reads as a held peak/valley
# rather than "nothing to report".
_EXTREME = 0.35
# Valence swing that maps to full tide intensity (the felt range is [-1, 1]).
_FULL_SWING = 0.5


def compute_tide(history: Sequence[dict], cfg: MoodConfig) -> dict | None:
    """Name the mood's trajectory from the recent valence history.

    Parameters
    ----------
    history
        The rolling reading history (newest last), each item carrying a
        ``"valence"``. Only the last ``cfg.tide_window`` are considered.
    cfg
        Supplies ``tide_window`` (how many readings define the trajectory) and
        ``tide_delta`` (the swing that counts as rising/falling rather than flat).

    Returns
    -------
    dict | None
        ``{"stage": one of rising|peak|falling|valley, "intensity": 0..1}`` — or
        ``None`` when there is too little history, or the mood is both flat and
        near neutral (no tide worth mentioning).
    """
    vals = [
        float(h.get("valence", 0.0))
        for h in list(history)[-cfg.tide_window :]
        if isinstance(h, dict)
    ]
    if len(vals) < 3:
        return None

    recent = vals[-1]
    earlier = sum(vals[:-1]) / len(vals[:-1])
    swing = recent - earlier

    if swing >= cfg.tide_delta:
        stage = "rising"
        intensity = min(1.0, abs(swing) / _FULL_SWING)
    elif swing <= -cfg.tide_delta:
        stage = "falling"
        intensity = min(1.0, abs(swing) / _FULL_SWING)
    elif recent >= _EXTREME:
        stage = "peak"
        intensity = min(1.0, abs(recent) / _FULL_SWING)
    elif recent <= -_EXTREME:
        stage = "valley"
        intensity = min(1.0, abs(recent) / _FULL_SWING)
    else:
        # Flat and near neutral — no tide worth reporting.
        return None

    return {"stage": stage, "intensity": round(intensity, 3)}
