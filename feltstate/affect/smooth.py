"""feltstate.affect.smooth — top-label hysteresis, so the felt block stops flickering.

The continuous parts of the felt state are kept cache-stable by the coarse phrase
bands in the renderer. The discrete *labels* are the exception: a noisy source can
flip the top label every turn, which changes the rendered block (and busts the
prompt cache) for no real change in feeling. This applies a small hysteresis — a
new top label must persist for a few ticks before it replaces the shown one.
Secondary labels are free to update once the top is stable.

Pure function: the caller (the engine) holds the little bit of cross-tick state
(the committed labels, the pending candidate, and its streak).
"""

from __future__ import annotations


def smooth_labels(
    new_labels: list[str],
    committed: list[str],
    candidate: str | None,
    streak: int,
    n: int,
) -> tuple[list[str], str | None, int]:
    """Apply top-label hysteresis. Returns ``(labels_to_show, candidate, streak)``.

    **Rationale.**  A noisy source can flip the top label every tick, which
    changes the rendered felt block (busting the prompt cache) for no real change
    in feeling.  This function prevents that: a *new* top label must appear as the
    top for ``n`` consecutive ticks before it is accepted and committed.  Secondary
    labels (positions 1+) are free to update once the top is stable, so they track
    the source with only the top position gated.

    **State machine (caller carries the cross-tick state):**

    * ``committed`` — the label list last accepted and shown.
    * ``candidate`` — the label currently trying to unseat the committed top,
      or ``None`` if no challenge is in progress.
    * ``streak`` — how many consecutive ticks the candidate has led so far.

    **Transitions:**

    * No new labels → hold committed, clear candidate (return committed as-is).
    * No committed top yet, or new top equals committed top → accept full
      new list immediately, clear candidate.
    * New top differs from committed top:

      * If it equals the current candidate, increment streak.
      * Otherwise, start a fresh challenge: ``candidate = new_top``, ``streak = 1``.
      * If ``streak >= max(1, n)`` → commit the switch (accept new list, clear
        candidate).
      * Otherwise → keep showing the old committed list, return updated
        ``(candidate, streak)`` so the caller can carry them to the next tick.

    **Invariants:**

    * When ``n <= 1`` (hysteresis disabled), every new top commits immediately —
      ``streak >= max(1, 1) == 1`` is satisfied on the first sighting.
    * The returned labels list is always a *copy* (never the same object as
      ``new_labels`` or ``committed``), so the caller can mutate it freely.
    * ``streak`` in the returned tuple is always >= 0.
    * ``candidate`` in the returned tuple is ``None`` whenever the switch just
      committed or no challenge is active.

    Parameters
    ----------
    new_labels
        This turn's freshly estimated labels (most salient first).
    committed
        The labels shown last turn (what the renderer is currently displaying).
        Pass an empty list on the very first call.
    candidate, streak
        The label currently trying to take over, and how many consecutive ticks
        it has led.  Pass ``(None, 0)`` on the very first call.
    n
        How many consecutive ticks a new top label must lead before it is
        accepted (``cfg.label_smooth_ticks``).  ``n <= 1`` disables the
        hysteresis — any new top commits in one tick.

    Returns
    -------
    tuple[list[str], str | None, int]
        ``(labels_to_show, candidate, streak)`` — the labels to display this
        turn and the updated challenge state to carry to the next call.
    """
    new_top = new_labels[0] if new_labels else None
    cur_top = committed[0] if committed else None

    if new_top is None:
        return list(committed), None, 0  # no labels estimated -> hold what we had
    if cur_top is None or new_top == cur_top:
        # nothing committed yet, or the top is unchanged -> accept the full new list
        return list(new_labels), None, 0

    # A different top label is trying to take over.
    if new_top == candidate:
        streak += 1
    else:
        candidate, streak = new_top, 1
    if streak >= max(1, n):
        return list(new_labels), None, 0  # it has led long enough -> commit the switch
    return list(committed), candidate, streak  # not yet -> keep the old top
