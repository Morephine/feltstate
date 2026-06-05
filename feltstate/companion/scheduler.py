"""feltstate.companion.scheduler — the heartbeat that decides when to act.

Generalises the production proactive daemon: it owns a tick loop, a persistent
namespaced state dict, a priority-ordered list of pluggable
:class:`BehaviorSource` objects, and the busy hard-gate. Each tick it advances
the engine's idle decay, then asks each source (highest priority first) whether
it wants to fire *now*; the first one that returns a payload is dispatched.

All the application-specific parts — what endpoint to hit, what an introspection
prompt says, how to know the user is busy — live behind the adapters
(:class:`~feltstate.companion.presence.UserPresenceAdapter`,
:class:`~feltstate.companion.dispatch.BehaviorDispatcher`,
:class:`~feltstate.companion.topics.PendingTopicsStore`). feltstate ships the
loop, the gates, the state machine, and zero-dependency reference behaviours.
"""

from __future__ import annotations

import json
import threading
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from ..engine import Engine
from .dispatch import BehaviorDispatcher
from .gates import SchedulerConfig, is_busy
from .presence import UserPresenceAdapter
from .topics import PendingTopicsStore


class BehaviorSource(ABC):
    """A pluggable proactive behaviour. The scheduler asks each one, in priority
    order, whether it wants to fire now."""

    kind: str = "behavior"
    priority: int = 5  # 0 = highest

    @abstractmethod
    def propose(
        self,
        state: dict,
        now: datetime,
        presence: UserPresenceAdapter,
        cfg: SchedulerConfig,
    ) -> str | None:
        """Return a payload string to dispatch, or ``None`` to pass.

        Apply this behaviour's own gates here, and write its namespaced
        bookkeeping keys into ``state`` only when it actually fires (so a
        passed-over tick leaves no trace). Keys must not collide across kinds.
        """
        ...


class CompanionScheduler:
    """The heartbeat. Wraps an :class:`Engine`; owns the tick thread, the busy
    gate, the priority queue, resume-detection, and persistent state."""

    def __init__(
        self,
        eng: Engine,
        *,
        presence: UserPresenceAdapter,
        dispatcher: BehaviorDispatcher,
        sources: list[BehaviorSource],
        state_path: str | Path = "scheduler_state.json",
        cfg: SchedulerConfig | None = None,
        topics: PendingTopicsStore | None = None,
    ) -> None:
        self.eng = eng
        self.presence = presence
        self.dispatcher = dispatcher
        self.sources = sorted(sources, key=lambda s: s.priority)
        self.state_path = Path(state_path)
        self.cfg = cfg or SchedulerConfig()
        self.topics = topics
        self._state = self._load_state()
        self._thread: threading.Thread | None = None
        self._stop: threading.Event | None = None

    @property
    def state(self) -> dict:
        """The live scheduler state dict (namespaced keys)."""
        return self._state

    # -- state persistence ------------------------------------------------- #
    def _load_state(self) -> dict:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        return {"boot_ts": 0.0, "today_date": "", "today_count": 0, "last_trigger_ts": 0.0}

    def _save_state(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps(self._state, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass

    def _reset_count_if_new_day(self, now: datetime) -> None:
        today = now.strftime("%Y-%m-%d")
        if self._state.get("today_date") != today:
            self._state["today_date"] = today
            self._state["today_count"] = 0

    # -- the tick ---------------------------------------------------------- #
    def tick_once(self, now: datetime | None = None) -> str | None:
        """One scheduler iteration. Returns the kind that fired, or ``None``."""
        now = now or datetime.now()
        now_ts = now.timestamp()
        st = self._state
        if not st.get("boot_ts"):
            st["boot_ts"] = now_ts

        # Busy hard-gate: the user is mid-turn, so do nothing and do not decay —
        # real conversation ticks drive the engine then; idle decay is for quiet.
        if is_busy(self.presence):
            st["last_tick_ts"] = now_ts
            self._save_state()
            return None

        self._reset_count_if_new_day(now)

        # Resume detection: a long quiet gap between ticks means the app was
        # asleep; note it so sources (e.g. focus-duration) can reset their clocks.
        last_tick = float(st.get("last_tick_ts", 0.0))
        if last_tick and now_ts - last_tick > self.cfg.resume_gap_s:
            st["resumed_ts"] = now_ts
        st["last_tick_ts"] = now_ts

        # Advance the felt state on the quiet path: idle decay + tiredness rise.
        self.eng.tick([])

        for src in self.sources:
            try:
                payload = src.propose(st, now, self.presence, self.cfg)
            except Exception:
                continue
            if payload is None:
                continue
            try:
                delivered = self.dispatcher.dispatch(src.kind, payload)
            except Exception:
                delivered = False
            if delivered:
                self._save_state()
                return src.kind

        self._save_state()
        return None

    # -- lifecycle --------------------------------------------------------- #
    def start(self) -> None:
        """Spawn the daemon heartbeat thread (idempotent)."""
        if self._thread is not None:
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        assert self._stop is not None
        while not self._stop.is_set():
            try:
                self.tick_once()
            except Exception:
                pass  # a bad tick must never kill the heartbeat
            self._stop.wait(self.cfg.tick_interval_s)

    def stop(self) -> None:
        """Signal the heartbeat thread to stop and join it."""
        if self._stop is not None:
            self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
