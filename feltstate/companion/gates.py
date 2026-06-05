"""feltstate.companion.gates — the scheduler config + the gate chain.

Pure-function gates decide whether a proposed behaviour may fire *now*. Each
returns ``True`` to allow. They read a plain ``SchedulerState`` dict (namespaced
keys, see :mod:`~feltstate.companion.scheduler`) and a
:class:`~feltstate.companion.presence.UserPresenceAdapter`, and they **fail
open** on a presence error: a down probe must not freeze the companion (mirrors
the production daemon's choice — stay alive over a flaky dependency).

:class:`SchedulerConfig` defaults map one-to-one to the production proactive
daemon's constants; every one is overridable via kwargs or env in the app layer.
"""

from __future__ import annotations

from dataclasses import dataclass

from .presence import UserPresenceAdapter


@dataclass(frozen=True)
class SchedulerConfig:
    """Timing knobs for :class:`~feltstate.companion.scheduler.CompanionScheduler`."""

    tick_interval_s: float = 300.0  # how often the heartbeat checks
    min_gap_s: float = 1800.0  # min seconds between any two ordinary fires
    daily_max: int = 8  # max ordinary proactive fires per day
    user_idle_min_s: float = 3600.0  # don't initiate within this of a user message
    solitude_min_s: float = 1800.0  # introspection needs this much solitude
    boot_grace_s: float = 300.0  # quiet window after start (no fires)
    introspect_gap_s: float = 1800.0  # min seconds between introspections
    resume_gap_s: float = 600.0  # a quiet gap this long counts as "resumed"
    time_windows: tuple[tuple[int, int], ...] = ((8, 12), (12, 16), (16, 20), (20, 24))
    diary_window: tuple[int, int] = (15, 18)


def _safe_idle_seconds(presence: UserPresenceAdapter) -> float:
    """Seconds since the last user message, failing open to ``inf`` (a broken
    probe is treated as 'long idle' so the companion keeps living)."""
    try:
        return float(presence.seconds_since_last_user_message())
    except Exception:
        return float("inf")


def is_busy(presence: UserPresenceAdapter) -> bool:
    """Instant hard check. Fails open to ``False`` (a broken probe = not busy)."""
    try:
        return bool(presence.is_busy())
    except Exception:
        return False


def not_busy(presence: UserPresenceAdapter) -> bool:
    """Allow only when the user/agent is not mid-turn right now."""
    return not is_busy(presence)


def not_recently_talked(presence: UserPresenceAdapter, cfg: SchedulerConfig) -> bool:
    """Allow only when the user has been quiet for at least ``user_idle_min_s``."""
    return _safe_idle_seconds(presence) >= cfg.user_idle_min_s


def solitude_ok(presence: UserPresenceAdapter, cfg: SchedulerConfig) -> bool:
    """Allow only after at least ``solitude_min_s`` of solitude (for introspection)."""
    return _safe_idle_seconds(presence) >= cfg.solitude_min_s


def not_in_daily_quota(state: dict, cfg: SchedulerConfig) -> bool:
    """Allow only while today's ordinary-fire count is under ``daily_max``."""
    return int(state.get("today_count", 0)) < cfg.daily_max


def not_in_gap(state: dict, now_ts: float, cfg: SchedulerConfig) -> bool:
    """Allow only when ``min_gap_s`` has passed since the last ordinary fire."""
    return now_ts - float(state.get("last_trigger_ts", 0.0)) >= cfg.min_gap_s


def past_boot_grace(state: dict, now_ts: float, cfg: SchedulerConfig) -> bool:
    """Allow only after the post-start quiet window (no fire-on-boot burst)."""
    return now_ts - float(state.get("boot_ts", 0.0)) >= cfg.boot_grace_s
