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

    Parameters
    ----------
    new_labels
        This turn's freshly measured labels (most salient first).
    committed
        The labels shown last turn (what the renderer is currently displaying).
    candidate, streak
        The label currently trying to take over and how many consecutive ticks
        it has led. Carry these forward between calls.
    n
        How many consecutive ticks a new top label must lead before it is accepted
        (``cfg.label_smooth_ticks``). ``n <= 1`` disables the hysteresis.

    Returns
    -------
    tuple[list[str], str | None, int]
        The labels to show this turn, plus the updated ``(candidate, streak)``.
    """
    new_top = new_labels[0] if new_labels else None
    cur_top = committed[0] if committed else None

    if new_top is None:
        return list(committed), None, 0  # nothing measured -> hold what we had
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
