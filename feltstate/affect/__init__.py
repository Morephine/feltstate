"""feltstate.affect — the dynamics layer.

This package integrates the per-turn :class:`~feltstate.state.AffectDelta`
readings into a persistent felt state. It has three pieces, each a pure-function
module operating on the schemas from :mod:`feltstate.state`:

* :mod:`~feltstate.affect.pressure` — the five-bar pressure cooker and its
  release dynamics (:func:`step`, :func:`compute_power`).
* :mod:`~feltstate.affect.traits` — slow temperament (asymmetric EWMA with
  baseline pull) and the fast felt mood (:func:`update_traits`,
  :func:`update_mood`).
* :mod:`~feltstate.affect.imprint` — optional permanent imprints that survive
  decay and echo on recall (:class:`Imprint` and its helpers).

The sibling modules are imported defensively: a module that is still being
written should not break ``import feltstate.affect`` for the others. Symbols
become available in this namespace as soon as their module exists.
"""

from __future__ import annotations

from .pressure import compute_power, step

__all__ = ["step", "compute_power"]

# --- traits / mood dynamics (sibling module) ---
try:
    from .traits import update_mood, update_traits
except Exception:  # pragma: no cover - module may not be present yet
    pass
else:
    __all__ += ["update_traits", "update_mood"]

# --- permanent imprints (optional enhancement) ---
try:
    from .imprint import (
        Imprint,
        apply_trait_shift,
        check_echo,
        decay_imprints,
        ingest_milestones,
    )
except Exception:  # pragma: no cover - module may not be present yet
    pass
else:
    __all__ += [
        "Imprint",
        "ingest_milestones",
        "apply_trait_shift",
        "decay_imprints",
        "check_echo",
    ]
