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


@dataclass
class Tiredness:
    """The single sleep-pressure accumulator.

    ``level`` rises with ``arousal × elapsed`` and is reset to 0 by a dream.
    ``last_dream_ts`` stamps the last discharge (drives the refractory floor).
    ``last_update_ts`` lets :meth:`rise` integrate real elapsed time whether it is
    called on an active turn or on an idle check, without double-counting.
    """

    level: float = 0.0
    last_dream_ts: str | None = None
    last_update_ts: str | None = None

    def rise(self, arousal: float, now: datetime, cfg: TirednessConfig) -> None:
        """Accrue sleep pressure for the time elapsed since the last update.

        Rate is ``rise_k · arousal`` per hour, optionally self-accelerating by
        ``self_accel_alpha · level`` ("the tireder you are, the faster you fade").
        The first call just stamps the clock (no elapsed time to integrate yet).
        """
        if self.last_update_ts is not None:
            try:
                prev = datetime.fromisoformat(self.last_update_ts)
                dt_h = max(0.0, (now - prev).total_seconds() / 3600.0)
            except (ValueError, TypeError):
                dt_h = 0.0
            rate = cfg.rise_k * max(0.0, arousal) * (1.0 + cfg.self_accel_alpha * self.level)
            self.level = min(cfg.level_cap, self.level + rate * dt_h)
        self.last_update_ts = now.isoformat()

    def hours_since_dream(self, now: datetime) -> float:
        """Hours since the last dream; ``inf`` if it has never dreamed."""
        if not self.last_dream_ts:
            return float("inf")
        try:
            return (now - datetime.fromisoformat(self.last_dream_ts)).total_seconds() / 3600.0
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

    def to_dict(self) -> dict:
        return {
            "level": round(self.level, 4),
            "last_dream_ts": self.last_dream_ts,
            "last_update_ts": self.last_update_ts,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> Tiredness:
        d = d or {}
        return cls(
            level=float(d.get("level", 0.0)),
            last_dream_ts=d.get("last_dream_ts"),
            last_update_ts=d.get("last_update_ts"),
        )
