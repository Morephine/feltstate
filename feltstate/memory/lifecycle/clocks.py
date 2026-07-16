"""feltstate.memory.lifecycle.clocks — different kinds of memory age at
different speeds, and immortality is always declared, never accidental.

Human memory does not decay on one curve. A trauma outlives a warm afternoon by
years; a distilled life-lesson outlives the small facts it was drawn from. This
module gives each memory kind its own clock — the same decay shape, geared
slower or faster.

The two rules that matter:

* **No floor.** A "minimum intensity" floor quietly makes every memory
  immortal — nothing can ever be forgotten, the store only grows, and
  "forgetting" degrades into a display filter. Here a memory below the death
  line is *eligible to actually die* (see :mod:`.gc`).
* **Declared immortality only.** Two rules and no more: memories born above
  ``permanent_line`` intensity never decay (a privilege declared at birth),
  and unfingerprinted legacy memories are exempt from collection entirely
  (the mercy rule in :mod:`.gc` — a collector must never kill what it cannot
  trace). Everything else ages.

Negative memories age on a more durable curve than positive ones (the
asymmetry feltstate uses everywhere: good moods fade fast, bad ones linger —
that is what makes comfort *from someone* mean something).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

__all__ = [
    "ClockConfig",
    "current_intensity",
]


@dataclass(frozen=True)
class ClockConfig:
    """The gearbox. ``gears`` maps a memory kind to how many times slower than
    the base clock it runs; unknown kinds run at the base rate (1.0)."""

    base_lambda: float = 1.0 / 45.0  # base decay rate: ~45 days to noticeable fade
    permanent_line: float = 0.85  # born at/above this intensity -> never decays
    death_line: float = 0.05  # below this the memory may actually be collected
    beta_durable: float = 0.85  # age exponent for negative-valence memories (sticky)
    beta_fast: float = 1.0  # age exponent for positive/neutral ones
    gears: Mapping[str, float] = field(
        default_factory=lambda: {
            "trauma": 4.0,  # deep scars: 4x slower than base
            "distilled": 3.5,  # condensed life-lessons / crystallised memories
            "warmth": 3.0,  # deep positive imprints
            "fact": 1.0,  # ordinary distilled facts: the base clock
        }
    )


# One shared default config. ClockConfig is frozen (read-only), so a single
# module-level instance is safe to hand out as the default — no caller can
# mutate it, and it avoids constructing a fresh config on every call.
_DEFAULT_CLOCK = ClockConfig()


def current_intensity(
    base: float, age_days: float, kind: str, cfg: ClockConfig | None = None, valence: float = 0.0
) -> float:
    """Where this memory's intensity stands today.

    ``base`` is the intensity it was born with; ``kind`` picks the gear;
    ``valence`` (the memory's emotional sign) picks the durability exponent —
    negative memories decay on the stickier curve. No floor: the return value
    is allowed to reach the death line, and past it the memory is honestly
    eligible for collection.

    Inputs are validated as finite; a negative or non-finite ``base`` or
    ``age_days`` raises rather than silently producing a surprising intensity.
    A future timestamp (negative age) is treated as age 0 (born now), not
    frozen at an arbitrary value.

    Passing ``cfg=None`` (the default) uses the shared default clock.
    """
    if cfg is None:
        cfg = _DEFAULT_CLOCK
    for name, val in (("base", base), ("age_days", age_days), ("valence", valence)):
        if not isinstance(val, (int, float)) or isinstance(val, bool) or not math.isfinite(val):
            raise ValueError(f"{name} must be a finite number, got {val!r}")
    if base < 0:
        raise ValueError(f"base intensity must be >= 0, got {base}")
    if base >= cfg.permanent_line:
        return base  # declared permanent at birth
    gear = float(cfg.gears.get(kind, 1.0))
    if gear <= 0:
        raise ValueError(f"gear for {kind!r} must be > 0, got {gear}")
    lam = cfg.base_lambda / gear
    beta = cfg.beta_durable if valence < 0 else cfg.beta_fast
    return base * math.exp(-lam * (max(0.0, age_days) ** beta))
