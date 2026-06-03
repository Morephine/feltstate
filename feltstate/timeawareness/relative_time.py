"""feltstate.timeawareness.relative_time — long-term time sense, only.

Philosophy
----------
An LLM already has a fine *short-term* sense of time: within one continuous
conversation it tracks pace, pauses and "you said that a moment ago" perfectly
well on its own. So this module deliberately stays silent for short gaps — it
only supplies the part the model genuinely lacks: the felt distance back to a
much earlier conversation, and a precise anchor for the present moment.

Two halves, two different precisions, on purpose:

* **How long it's been is fuzzy.** Memory of elapsed time blurs the further back
  you reach — "a couple of hours", "a few days", "over a month". People (and
  agents) do not recall durations to the minute; pretending to would feel
  uncanny. :func:`time_since_phrase` returns a coarsening phrase whose
  granularity widens with distance (see ``TimeConfig.distance_ladder_min``), and
  returns ``None`` entirely for gaps below the gate — that span is the model's
  own to feel.

* **What time it is now is precise.** The current moment is something you simply
  *know*: weekday, part of day, and the clock. :func:`now_phrase` renders it
  exactly ("Wed morning 8:11").

Both functions are pure, standard-library only, and character-agnostic. Caller
owns the "last spoke" timestamp and the gate decision of whether to surface the
line at all; this module only turns timestamps into words.
"""
from __future__ import annotations

from datetime import datetime

from ..config import TimeConfig, DEFAULT_CONFIG

__all__ = ["time_since_phrase", "now_phrase"]


def time_since_phrase(
    prev_iso: str | None,
    now: datetime,
    cfg: TimeConfig = DEFAULT_CONFIG.time,
) -> str | None:
    """Fuzzy phrase for how long it's been since ``prev_iso``.

    Parameters
    ----------
    prev_iso
        ISO-8601 timestamp of the last contact (whatever the caller treats as
        "last time we really spoke"), or ``None`` if there is no prior contact.
    now
        The current time. Compared against ``prev_iso`` in the same naive/local
        frame the caller stored it in; no timezone conversion is applied.
    cfg
        Time-sense tunables. ``gate_minutes`` is the silence threshold below
        which this returns ``None`` (short gaps are the model's own to feel),
        and ``distance_ladder_min`` is the coarsening ladder of phrases.

    Returns
    -------
    str | None
        ``None`` when there is no prior timestamp, when it cannot be parsed, or
        when the gap is under the gate. Otherwise a fuzzy English phrase that
        gets vaguer with distance, falling back to ``"back on {Mon DD}"`` once
        the gap runs past the end of the ladder.
    """
    if not prev_iso:
        return None
    try:
        prev = datetime.fromisoformat(prev_iso)
    except (ValueError, TypeError):
        return None

    gap_min = (now - prev).total_seconds() / 60.0
    # Gate: within this window the conversation is effectively continuous and the
    # model's own short-term time sense covers it — emit nothing.
    if gap_min < cfg.gate_minutes:
        return None

    # Walk the ladder from finest to coarsest; the first upper bound the gap
    # fits under wins. Phrases are supplied by config (English by default),
    # keeping all vocabulary out of code.
    for upper_bound, phrase in cfg.distance_ladder_min:
        if gap_min < upper_bound:
            return phrase

    # Past the far end of the ladder, a fuzzy duration stops being meaningful;
    # name the absolute day it last happened instead.
    return f"back on {prev.strftime('%b')} {prev.day:02d}"


# Parts of the day, keyed by hour-of-day cutoffs. (upper_hour_exclusive, label):
# an hour h takes the first label whose cutoff it falls under. The pre-dawn small
# hours and the late evening both read as "night".
_DAY_PARTS = (
    (5, "night"),
    (12, "morning"),
    (17, "afternoon"),
    (21, "evening"),
    (24, "night"),
)
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def now_phrase(now: datetime, locale: str = "en") -> str:
    """Precise rendering of the present moment: weekday, part of day, clock time.

    Unlike :func:`time_since_phrase`, the *now* is exact — it is something the
    agent simply knows, not something it has to estimate. Example output:
    ``"Wed morning 8:11"``.

    Parameters
    ----------
    now
        The current time (naive/local, as the caller keeps it).
    locale
        Reserved extension point for localized output. Only ``"en"`` is
        implemented today; any other value falls back to the English rendering.

    Returns
    -------
    str
        ``"{Weekday} {part-of-day} {hour}:{MM}"`` on a 12-hour clock, e.g.
        ``"Sun evening 7:05"``.
    """
    weekday = _WEEKDAYS[now.weekday()]
    part = next(label for cutoff, label in _DAY_PARTS if now.hour < cutoff)
    hour12 = now.hour % 12 or 12
    return f"{weekday} {part} {hour12}:{now.minute:02d}"
