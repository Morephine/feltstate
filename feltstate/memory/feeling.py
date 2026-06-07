"""feltstate.memory.feeling — evidence-weighted emotion for a stored fact (M1).

A fact's *salience* (how strongly it is held) still reinforces with repetition —
that is the intended behaviour and is left untouched. This module adds a
*separate* signal: how the fact **feels**, as an evidence-weighted confidence
distribution over ``{positive, negative, neutral}``.

Why a distribution and not a number. Repeating a fact with a real emotion makes
that feeling more confident and harder to flip; repeating a *flat* one — a
catch-phrase, filler, a tic — keeps it neutral instead of letting sheer frequency
masquerade as meaning. That is the catch-phrase filter: a thing said a hundred
times with no feeling carries ~zero emotional *charge*, so a caller can decline to
surface it without ever capping the reinforce of things that do matter.

Pure functions, standard library only. The update is Bayesian: a fact starts with
a small prior evidence weight (a young feeling moves fast); each observation folds
in proportional to its own weight and accrues evidence, so a settled feeling gains
inertia and one stray message can't overturn it — a standard Bayesian
evidence-weighting, reduced to its load-bearing arithmetic.
"""

from __future__ import annotations

import math

__all__ = ["neutral_profile", "observe", "blend", "derive"]

# A profile is a 3-tuple (pos, neg, neu) summing to 1.0.
Profile = tuple


def neutral_profile() -> tuple[float, float, float]:
    """A fact about which nothing emotional is known yet: all neutral."""
    return (0.0, 0.0, 1.0)


def observe(valence: float) -> tuple[float, float, float]:
    """Turn one emotion reading into a soft ``(pos, neg, neu)`` distribution.

    A strong valence puts its mass on pos or neg and leaves little neutral; a flat
    reading (``valence ≈ 0``) is almost all neutral — so a run of flat mentions
    keeps a fact neutral no matter how often it recurs.
    """
    v = max(-1.0, min(1.0, float(valence)))
    return (max(0.0, v), max(0.0, -v), 1.0 - abs(v))


def blend(
    profile: tuple[float, float, float],
    weight: float,
    ob: tuple[float, float, float],
    signal_weight: float,
) -> tuple[tuple[float, float, float], float]:
    """Fold observation ``ob`` (carrying ``signal_weight``) into ``profile``
    (carrying accrued ``weight``). Returns ``(new_profile, new_weight)``::

        new = (profile·w + ob·s) / (w + s);   new_w = w + s

    High accrued weight → one observation barely moves it (inertia); low weight (a
    young fact) → it moves fast. ``signal_weight`` is naturally the reading's
    confidence, so an unsure reading nudges less.
    """
    w = max(0.0, float(weight))
    s = max(0.0, float(signal_weight))
    tot = w + s
    if tot <= 0.0:
        return profile, 0.0
    new = (
        (profile[0] * w + ob[0] * s) / tot,
        (profile[1] * w + ob[1] * s) / tot,
        (profile[2] * w + ob[2] * s) / tot,
    )
    return new, tot


def derive(profile: tuple[float, float, float]) -> dict:
    """Read a profile back as the felt signals a caller can use.

    * ``valence`` = ``pos − neg`` — how the fact leans (−1..1).
    * ``charge``  = ``pos + neg`` = ``1 − neu`` — how emotionally loaded it is
      (0..1). The catch-phrase filter: flat facts have ~0 charge however often
      repeated.
    * ``entropy`` = Shannon entropy over the three (0..~1.58) — an ambivalence /
      uncertainty signal: a fact pulled both warm and cold reads high-entropy.
    """
    pos, neg, neu = profile
    ent = 0.0
    for p in (pos, neg, neu):
        if p > 0.0:
            ent -= p * math.log2(p)
    return {
        "valence": round(pos - neg, 4),
        "charge": round(pos + neg, 4),
        "entropy": round(ent, 4),
    }
