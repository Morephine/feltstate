"""feltstate.sleep — sleep pressure: the drive that decides *when* to dream.

A single accumulator ("tiredness") that rises with activity while the agent is
awake and discharges to zero when it sleeps (dreams). This is the homeostatic
half of the two-process model of sleep (Process S): pressure builds the longer
and harder you are awake, and a sleep clears it.

Three deliberate choices:

* **Driven by arousal, not the clock.** The rate is ``rise_k · arousal`` — an
  activated stretch tires the agent faster than a calm one — so *when* it sleeps
  reflects how it lived that day, not the time of day. (A quiet day still drifts
  toward sleep slowly, because arousal has a floor.)
* **One value, not two.** A body model usually splits "sleepiness" and "fatigue";
  for deciding when to dream the distinction buys nothing, so this is a single
  number. "The tireder you are, the sleepier you get" is then automatic, and an
  optional ``self_accel_alpha`` lets exhaustion compound.
* **Tool, not controller.** It produces a *ready-to-dream* reading; the agent
  still calls :meth:`~feltstate.engine.Engine.maybe_dream`. And a hard
  ``refractory_hours`` floor guarantees a sane cap (no dreaming three times a day)
  no matter how fast pressure climbs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .config import TirednessConfig


def _same_frame(prev: datetime, now: datetime) -> datetime:
    """Return ``prev`` coerced into ``now``'s naive/aware frame (2026-07-18).

    Subtracting a naive datetime from an aware one raises TypeError. Here that
    error used to be swallowed by the callers' except-clauses, which is worse
    than crashing: ``rise()`` read it as "no elapsed time" *and then advanced
    the clock stamp*, permanently eating the accrued interval, while
    ``hours_since_dream()`` read it as "never dreamed", bypassing the
    refractory floor. Treat a legacy stamp as being in ``now``'s frame —
    best-effort arithmetic beats silently corrupting the sleep economy.
    """
    if prev.tzinfo is None and now.tzinfo is not None:
        return prev.replace(tzinfo=now.tzinfo)
    if prev.tzinfo is not None and now.tzinfo is None:
        return prev.replace(tzinfo=None)
    return prev


@dataclass
class Tiredness:
    """The single sleep-pressure accumulator.

    ``level`` rises with ``arousal × elapsed`` and is reset to 0 by a dream.
    ``last_dream_ts`` stamps the last discharge (drives the refractory floor).
    ``last_update_ts`` lets :meth:`rise` integrate real elapsed time whether it is
    called on an active turn or on an idle check, without double-counting.
    ``last_arousal`` remembers the arousal that governed the interval now ending,
    so an elapsed span is integrated at the arousal it was actually lived at (the
    *prior* interval's arousal), not at whatever arousal the current call reports.
    """

    level: float = 0.0
    last_dream_ts: str | None = None
    last_update_ts: str | None = None
    last_arousal: float | None = None

    def rise(self, arousal: float, now: datetime, cfg: TirednessConfig) -> None:
        """Accrue sleep pressure for the interval since the last update.

        The interval ``[last_update, now]`` is integrated at the arousal that was
        in effect *during* it — the arousal reported at the previous update
        (:attr:`last_arousal`) — not at the arousal passed on *this* call (finding
        #14). Otherwise ten calm hours followed by a single high-arousal message
        would retroactively integrate all ten hours at the high arousal, as if the
        agent had been activated the whole time. ``arousal`` here governs the
        *next* interval; it is stored for the following :meth:`rise`.

        Base rate is ``rise_k · arousal`` per hour. With ``self_accel_alpha > 0``
        the rate self-accelerates ("the tireder you are, the faster you fade"):
        ``dL/dt = c · (1 + α·L)``. That is integrated over the elapsed span in
        **closed form** — ``L <- ((1 + α·L)·exp(α·c·dt) - 1) / α`` — rather than by
        a per-call Euler step, so the result depends only on how long and how hard
        the agent was awake, not on how often :meth:`rise` was called (finding
        #15). With ``α == 0`` this reduces exactly to the plain ``L <- L + c·dt``.
        Both forms are frequency-invariant: integrating a span in one step or many
        yields the same level. The first call just stamps the clock (no elapsed
        time to integrate yet).
        """
        if self.last_update_ts is not None:
            try:
                prev = _same_frame(datetime.fromisoformat(self.last_update_ts), now)
                dt_h = max(0.0, (now - prev).total_seconds() / 3600.0)
            except (ValueError, TypeError):
                dt_h = 0.0
            # Integrate the elapsed interval at the arousal it was lived at. On a
            # freshly loaded state with no recorded prior (last_arousal is None),
            # fall back to this call's arousal for that one interval.
            prior_arousal = self.last_arousal if self.last_arousal is not None else arousal
            c = cfg.rise_k * max(0.0, prior_arousal)
            alpha = cfg.self_accel_alpha
            if alpha > 0.0 and dt_h > 0.0:
                # Exact solution of dL/dt = c·(1 + α·L) over dt_h (composes).
                import math

                grown = (1.0 + alpha * self.level) * math.exp(alpha * c * dt_h)
                new_level = (grown - 1.0) / alpha
            else:
                new_level = self.level + c * dt_h
            self.level = min(cfg.level_cap, new_level)
        self.last_update_ts = now.isoformat()
        self.last_arousal = max(0.0, float(arousal))

    def hours_since_dream(self, now: datetime) -> float:
        """Hours since the last dream; ``inf`` if it has never dreamed."""
        if not self.last_dream_ts:
            return float("inf")
        try:
            prev = _same_frame(datetime.fromisoformat(self.last_dream_ts), now)
            return (now - prev).total_seconds() / 3600.0
        except (ValueError, TypeError):
            return float("inf")

    def ready(self, now: datetime, idle_minutes: float, cfg: TirednessConfig) -> bool:
        """True when it is time to dream: tired enough, alone long enough, and past
        the refractory floor since the last dream. All three must hold."""
        return (
            self.level >= cfg.threshold
            and idle_minutes >= cfg.idle_gate_minutes
            and self.hours_since_dream(now) >= cfg.refractory_hours
        )

    def discharge(self, now: datetime) -> None:
        """Sleep: pressure clears to zero and the refractory clock restarts."""
        self.level = 0.0
        self.last_dream_ts = now.isoformat()
        self.last_update_ts = now.isoformat()
        # The arousal that governed the pre-dream interval no longer applies; the
        # post-dream interval starts unmeasured until the next rise() reports one.
        self.last_arousal = None

    def to_dict(self) -> dict:
        return {
            "level": round(self.level, 4),
            "last_dream_ts": self.last_dream_ts,
            "last_update_ts": self.last_update_ts,
            "last_arousal": self.last_arousal,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> Tiredness:
        d = d or {}
        raw_arousal = d.get("last_arousal")
        return cls(
            level=float(d.get("level", 0.0)),
            last_dream_ts=d.get("last_dream_ts"),
            last_update_ts=d.get("last_update_ts"),
            last_arousal=None if raw_arousal is None else float(raw_arousal),
        )
