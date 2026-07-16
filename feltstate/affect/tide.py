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
# rather than "nothing to report".  Below this and not moving -> flat, return None.
_EXTREME = 0.35
# Valence swing that maps to *full* tide intensity of 1.0. Swings beyond this
# saturate at 1.0; smaller swings scale linearly. The felt valence range is [-1, 1],
# so 0.5 is a half-range swing — noticeably large, but reachable in a few turns.
_FULL_SWING = 0.5


def compute_tide(history: Sequence[dict], cfg: MoodConfig) -> dict | None:
    """Name the mood's trajectory from the recent valence history.

    **Algorithm.**  Takes the last ``cfg.tide_window`` readings (newest last) and
    computes a *swing*: the most recent valence minus the mean of all preceding
    readings in the window.  Stages are assigned in priority order:

    1. **rising** — swing >= ``cfg.tide_delta``: the mood is climbing.
    2. **falling** — swing <= ``-cfg.tide_delta``: the mood is sinking.
    3. **peak** — mood is flat but high (recent >= :data:`_EXTREME`): a held high.
    4. **valley** — mood is flat but low (recent <= ``-_EXTREME``): a held low.
    5. **None** — flat *and* near neutral (no tide worth mentioning).

    The directional stages (rising/falling) take precedence over the level stages
    (peak/valley), so a valence just rising through the extreme band reads
    ``rising``, not ``peak``.  Intensity scales linearly with the magnitude of the
    swing (or the level for peak/valley), saturating at 1.0 at :data:`_FULL_SWING`.

    **Invariants:**

    * The function is **read-only** — it never mutates the history or state.
    * Returns ``None`` (not a dict) when there is too little history (< 3 readings).
    * ``intensity`` is always in ``[0.0, 1.0]`` and rounded to three decimal places.
    * The ``stage`` key is always one of ``"rising"``, ``"falling"``,
      ``"peak"``, or ``"valley"`` when a dict is returned.

    Parameters
    ----------
    history
        The rolling reading history (newest last), each item a dict carrying at
        least a ``"valence"`` key.  Non-dict items are silently skipped.  Only
        the last ``cfg.tide_window`` items are considered.
    cfg
        Supplies ``tide_window`` (how many readings define the trajectory) and
        ``tide_delta`` (the minimum absolute swing that counts as rising or
        falling rather than flat).

    Returns
    -------
    dict | None
        ``{"stage": one of rising|peak|falling|valley, "intensity": 0.0..1.0}``
        — or ``None`` when there is too little history, or the mood is both flat
        and near neutral (no tide worth mentioning).
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
