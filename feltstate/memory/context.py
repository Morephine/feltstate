"""feltstate.memory.context — expand a distilled memory back to its source turns.

A canon fact is a *distillation*; the original exchange it came from lives in
your conversation transcript. When you want the agent to "open the original" —
to see the ±N turns around where a fact was learned — this resolves them.

Read-only, zero-dependency, and **source-agnostic**: you hand it the turns and an
anchor, and it returns the surrounding window. feltstate never owns your
transcript's path or format. The anchor can be a plain :class:`~datetime`-style
timestamp string (e.g. a canon entry's ``ts``) or an integer index — so a
distilled fact resolves back to roughly where it was said, even without a
dedicated "source" field on the fact.
"""

from __future__ import annotations

import json
from pathlib import Path


def _ts_of(turn: dict) -> str:
    """Return a turn's ``timestamp`` as a string, or ``""`` if absent/falsy."""
    return str(turn.get("timestamp", "") or "")


def get_turn_context(
    turns: list[dict],
    anchor: str | int,
    *,
    before: int = 2,
    after: int = 2,
) -> dict:
    """Return the ±N turns around ``anchor`` within ``turns`` (oldest first).

    Parameters
    ----------
    turns:
        A list of turn dicts, chronological. Each is anything carrying a
        ``timestamp`` (and usually ``role`` / ``content``); no schema is imposed
        beyond reading ``timestamp`` for string anchors.
    anchor:
        Either an **int index** into ``turns``, or a **timestamp string**. A
        leading ``"chat:"`` is stripped (so a ``"chat:2026-06-06T10:02"`` source
        works as-is). A string anchor matches by its minute prefix
        (``YYYY-MM-DDTHH:MM``); with no exact minute, it falls back to the latest
        turn at/just before the anchor (``approx=True``).
    before, after:
        How many turns to include on each side (clamped to the list bounds). For
        the common "±5 turns" view, pass ``before=5, after=5``.

    Returns
    -------
    ``{"ok": True, "turns": [...], "match_index": int, "n_total": int,
    "after_available": int, "approx": bool}`` on success, or
    ``{"ok": False, "reason": str}``.
    """
    if not turns:
        return {"ok": False, "reason": "no turns"}

    idx: int | None = None
    approx = False

    if isinstance(anchor, int):
        if not (0 <= anchor < len(turns)):
            return {"ok": False, "reason": f"index {anchor} out of range"}
        idx = anchor
    else:
        ts = str(anchor or "").split("chat:", 1)[-1].strip()
        if not ts:
            return {"ok": False, "reason": "empty anchor"}
        minute = ts[:16]
        for i, t in enumerate(turns):  # 1) exact minute match
            if _ts_of(t)[:16] == minute:
                idx = i
                break
        if idx is None:  # 2) fallback: latest turn at/just before the anchor
            best: int | None = None
            for i, t in enumerate(turns):
                tts = _ts_of(t)
                if tts and tts <= ts and (best is None or tts > _ts_of(turns[best])):
                    best = i
            if best is not None:
                idx, approx = best, True
        if idx is None:
            return {"ok": False, "reason": f"no turn near {ts}"}

    seg = turns[max(0, idx - before) : idx + after + 1]
    return {
        "ok": True,
        "turns": seg,
        "match_index": idx,
        "n_total": len(turns),
        "after_available": len(turns) - 1 - idx,
        "approx": approx,
    }


def get_turn_range_context(
    turns: list[dict],
    start: str,
    end: str,
    *,
    before: int = 2,
    after: int = 2,
) -> dict:
    """Return the transcript window around an inclusive ``start``–``end`` range.

    This is the range-aware companion to :func:`get_turn_context` and is useful
    for provenance pointers whose source covers more than one turn. Timestamps
    are compared as strings, matching the existing source-agnostic contract; use
    consistently formatted, sortable timestamps (normally ISO-8601).

    When one or more turns fall inside the range, the result includes the whole
    source span plus ``before`` / ``after`` surrounding turns and reports
    ``source_start_index`` / ``source_end_index``. If the range contains no turn,
    the function falls back to :func:`get_turn_context` at ``start`` and marks the
    result ``approx=True``.
    """
    if not turns:
        return {"ok": False, "reason": "no turns"}

    t0 = str(start or "").split("chat:", 1)[-1].strip()
    t1 = str(end or "").split("chat:", 1)[-1].strip()
    if not t0 or not t1:
        return {"ok": False, "reason": "empty range boundary"}
    if t1 < t0:
        return {"ok": False, "reason": "range end precedes start"}

    matched = [i for i, turn in enumerate(turns) if t0 <= _ts_of(turn) <= t1]
    if not matched:
        fallback = get_turn_context(turns, t0, before=before, after=after)
        if fallback.get("ok"):
            fallback = dict(fallback)
            fallback["approx"] = True
            fallback["range_fallback"] = True
            fallback["source_start_index"] = fallback["match_index"]
            fallback["source_end_index"] = fallback["match_index"]
        return fallback

    first, last = matched[0], matched[-1]
    before_n, after_n = max(0, int(before)), max(0, int(after))
    lo, hi = max(0, first - before_n), min(len(turns), last + after_n + 1)
    return {
        "ok": True,
        "turns": turns[lo:hi],
        "match_index": first,
        "source_start_index": first,
        "source_end_index": last,
        "source_turns": turns[first : last + 1],
        "n_total": len(turns),
        "after_available": len(turns) - 1 - last,
        "approx": False,
        "range_fallback": False,
    }


def load_turns(
    path: str | Path,
    *,
    roles: tuple[str, ...] = ("user", "assistant", "human", "ai"),
) -> list[dict]:
    """Convenience loader for the common transcript shape: a JSON file holding a
    list of turn dicts. Keeps only dicts whose ``role`` is in ``roles`` (dropping
    metadata rows). Path and format are yours — this just covers the usual case;
    for anything else build the ``turns`` list however you like and pass it to
    :func:`get_turn_context`. Returns ``[]`` on any read/parse error.
    """
    p = Path(path)
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(obj, list):
        return []
    return [t for t in obj if isinstance(t, dict) and t.get("role") in roles]
