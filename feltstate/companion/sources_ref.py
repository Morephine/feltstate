"""feltstate.companion.sources_ref — zero-dependency reference behaviours.

Generic versions of the production daemon's proactive behaviours, with all the
prompt text and HTTP stripped out: the application supplies payloads / callables,
feltstate supplies the timing + gating. Mix and match; the scheduler orders them
by ``priority``.

The eight behaviours mirror the production daemon: a pending-topic queue, daily
time windows, focus-duration nudges, random openers, rare self-expression
bursts, solitude introspection, dreaming, and a daily diary. Ordinary behaviours
share the daily-quota / min-gap / user-idle gates; introspection and dreaming
run on their own (solitude / sleep-pressure) clocks and do not spend the quota.
"""

from __future__ import annotations

import random
from collections.abc import Callable

from ..engine import Engine
from .gates import (
    SchedulerConfig,
    not_in_daily_quota,
    not_in_gap,
    not_recently_talked,
    past_boot_grace,
    solitude_ok,
)
from .presence import UserPresenceAdapter
from .scheduler import BehaviorSource
from .topics import PendingTopicsStore


def _idle_seconds(presence: UserPresenceAdapter) -> float:
    try:
        return float(presence.seconds_since_last_user_message())
    except Exception:
        return float("inf")


def _ordinary_ok(
    state: dict, now_ts: float, presence: UserPresenceAdapter, cfg: SchedulerConfig
) -> bool:
    """The shared gate chain for ordinary proactive behaviours."""
    return (
        past_boot_grace(state, now_ts, cfg)
        and not_recently_talked(presence, cfg)
        and not_in_daily_quota(state, cfg)
        and not_in_gap(state, now_ts, cfg)
    )


def _mark_ordinary_fire(state: dict, now_ts: float) -> None:
    state["last_trigger_ts"] = now_ts
    state["today_count"] = int(state.get("today_count", 0)) + 1


class PendingTopicsSource(BehaviorSource):
    """Highest priority: raise the oldest note the companion left itself."""

    kind = "pending"
    priority = 0

    def __init__(self, topics: PendingTopicsStore) -> None:
        self.topics = topics

    def propose(self, state, now, presence, cfg):
        if not _ordinary_ok(state, now.timestamp(), presence, cfg):
            return None
        # Read only — the topic is not marked consumed until commit(), so a
        # failed delivery leaves it pending to be raised again next tick.
        return self.topics.read_oldest_unconsumed()

    def commit(self, state, now, cfg):
        # Delivery confirmed: consume the same oldest-unconsumed topic we
        # proposed (stable within a tick — no other writer this heartbeat).
        topic = self.topics.read_oldest_unconsumed()
        if topic is None:
            return
        self.topics.mark_consumed(topic)
        _mark_ordinary_fire(state, now.timestamp())


class TimeWindowSource(BehaviorSource):
    """Fire a payload once per day inside an hour window (lunch check-in, etc.)."""

    kind = "time_window"
    priority = 1

    def __init__(self, windows: list[tuple[int, int, str]]) -> None:
        # windows: [(start_hour, end_hour, payload), ...]
        self.windows = list(windows)

    def _eligible_window(self, state, now) -> int | None:
        """Index of the first window open now that hasn't fired today, else None."""
        today = now.strftime("%Y-%m-%d")
        hour = now.hour
        fired = state.get("time_window_fired", {})
        for i, (start, end, _payload) in enumerate(self.windows):
            if start <= hour < end and fired.get(str(i)) != today:
                return i
        return None

    def propose(self, state, now, presence, cfg):
        if not _ordinary_ok(state, now.timestamp(), presence, cfg):
            return None
        i = self._eligible_window(state, now)
        if i is None:
            return None
        return self.windows[i][2]

    def commit(self, state, now, cfg):
        i = self._eligible_window(state, now)
        if i is None:
            return
        state.setdefault("time_window_fired", {})[str(i)] = now.strftime("%Y-%m-%d")
        _mark_ordinary_fire(state, now.timestamp())


class FocusDurationSource(BehaviorSource):
    """Nudge after the user has stayed on one app for a long stretch."""

    kind = "focus"
    priority = 2

    def __init__(
        self,
        app_detector: Callable[[], str | None],
        payload_for: Callable[[str, float], str | None],
        *,
        min_focus_s: float = 1800.0,
    ) -> None:
        self.app_detector = app_detector
        self.payload_for = payload_for
        self.min_focus_s = min_focus_s

    def propose(self, state, now, presence, cfg):
        now_ts = now.timestamp()
        try:
            app = self.app_detector()
        except Exception:
            app = None
        # Focus-clock tracking is pure observation of the world — it advances
        # regardless of whether this tick fires, so it stays out of commit().
        if not app:
            state["focus_app"] = ""
            return None
        if state.get("resumed_ts"):  # app was asleep; reset the focus clock
            state["focus_app"] = ""
            state["resumed_ts"] = 0
        if state.get("focus_app") != app:
            state["focus_app"] = app
            state["focus_app_ts"] = now_ts
            state["focus_fired_app"] = ""
            return None
        if not _ordinary_ok(state, now_ts, presence, cfg):
            return None
        if now_ts - float(state.get("focus_app_ts", now_ts)) < self.min_focus_s:
            return None
        if state.get("focus_fired_app") == app:  # once per stretch
            return None
        try:
            payload = self.payload_for(app, now_ts - float(state.get("focus_app_ts", now_ts)))
        except Exception:
            payload = None
        if not payload:
            return None
        # The once-per-stretch flag and the quota are consumed only on delivery.
        return payload

    def commit(self, state, now, cfg):
        app = state.get("focus_app")
        if not app:
            return
        state["focus_fired_app"] = app
        _mark_ordinary_fire(state, now.timestamp())


class RandomSource(BehaviorSource):
    """A random opener: with probability ``p`` each eligible tick, say something."""

    def __init__(
        self,
        payloads: list[str],
        *,
        probability: float = 0.15,
        rng: random.Random | None = None,
        kind: str = "random",
        priority: int = 3,
    ) -> None:
        self.payloads = list(payloads)
        self.probability = probability
        self.rng = rng or random.Random()
        self.kind = kind
        self.priority = priority

    def propose(self, state, now, presence, cfg):
        if not self.payloads:
            return None
        if not _ordinary_ok(state, now.timestamp(), presence, cfg):
            return None
        if self.rng.random() >= self.probability:
            return None
        # Roll the payload now; the quota slot is spent only on confirmed delivery.
        return self.rng.choice(self.payloads)

    def commit(self, state, now, cfg):
        _mark_ordinary_fire(state, now.timestamp())


class BurstSource(RandomSource):
    """A rarer, self-initiated line — the companion piping up on its own."""

    def __init__(
        self,
        payloads: list[str],
        *,
        probability: float = 0.04,
        rng: random.Random | None = None,
    ) -> None:
        super().__init__(payloads, probability=probability, rng=rng, kind="burst", priority=4)


class IntrospectSource(BehaviorSource):
    """Silent introspection: once per time-window per day, after enough solitude.

    Does **not** spend the daily proactive quota. ``payload`` is handed to the
    dispatcher (``""`` = a silent introspection the application runs internally).
    """

    kind = "introspect"
    priority = 5

    def __init__(self, payload: str = "", *, windows: list[tuple[int, int]] | None = None) -> None:
        self.payload = payload
        self.windows = windows

    def _windows(self, cfg):
        return self.windows if self.windows is not None else cfg.time_windows

    def _eligible_window(self, state, now, cfg) -> int | None:
        today = now.strftime("%Y-%m-%d")
        hour = now.hour
        fired = state.get("introspect_window_fired", {})
        for i, (start, end) in enumerate(self._windows(cfg)):
            if start <= hour < end and fired.get(str(i)) != today:
                return i
        return None

    def propose(self, state, now, presence, cfg):
        now_ts = now.timestamp()
        if not past_boot_grace(state, now_ts, cfg):
            return None
        if not solitude_ok(presence, cfg):
            return None
        if now_ts - float(state.get("introspect_last_ts", 0.0)) < cfg.introspect_gap_s:
            return None
        if self._eligible_window(state, now, cfg) is None:
            return None
        # Anti-spam markers (last-ts + window flag) are set on delivery, so a
        # failed introspection this tick is retried rather than silently skipped.
        return self.payload

    def commit(self, state, now, cfg):
        i = self._eligible_window(state, now, cfg)
        if i is None:
            return
        state.setdefault("introspect_window_fired", {})[str(i)] = now.strftime("%Y-%m-%d")
        state["introspect_last_ts"] = now.timestamp()


class DreamSource(BehaviorSource):
    """Dream when sleep-pressure says so. Wraps :meth:`Engine.maybe_dream`,
    which applies its own level / idle / refractory gates and nudges the mood
    inside the engine. Fires silently (the residue is already applied)."""

    kind = "dream"
    priority = 6

    def __init__(
        self,
        eng: Engine,
        *,
        fragments: list | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.eng = eng
        self.fragments = fragments
        self.rng = rng

    def propose(self, state, now, presence, cfg):
        idle_s = _idle_seconds(presence)
        idle_min = idle_s / 60.0 if idle_s != float("inf") else 1e9
        dreamt = self.eng.maybe_dream(
            idle_minutes=idle_min, now=now, fragments=self.fragments, rng=self.rng
        )
        if dreamt is None:
            return None
        return ""  # silent: the dream already nudged the mood inside the engine


class DiarySource(BehaviorSource):
    """Write a daily diary once, inside a window, after the user has gone quiet.

    ``diary_runner`` is a caller-supplied ``() -> str | None`` that writes the
    entry (wherever the app keeps it) and returns an optional to-user line. Does
    not spend the daily proactive quota.
    """

    kind = "diary"
    priority = 5

    def __init__(
        self, diary_runner: Callable[[], str | None], *, rng: random.Random | None = None
    ) -> None:
        self.diary_runner = diary_runner
        self.rng = rng or random.Random()

    def propose(self, state, now, presence, cfg):
        today = now.strftime("%Y-%m-%d")
        if state.get("diary_done_date") == today:
            return None
        start, end = cfg.diary_window
        if not (start <= now.hour < end):
            return None
        if not past_boot_grace(state, now.timestamp(), cfg):
            return None
        if not not_recently_talked(presence, cfg):
            return None
        try:
            line = self.diary_runner()
        except Exception:
            line = None
        state["diary_done_date"] = today  # mark even on failure: don't retry-spam
        return line if line is not None else ""
