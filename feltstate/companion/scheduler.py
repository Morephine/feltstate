"""feltstate.companion.scheduler — the heartbeat that decides when to act.

Owns a tick loop, a persistent namespaced state dict, a priority-ordered list
of pluggable :class:`BehaviorSource` objects, and the busy hard-gate. Each
tick it advances the engine's idle decay, then asks each source (highest
priority first) whether it wants to fire *now*; the first one that returns a
payload is dispatched.

All the application-specific parts — what endpoint to hit, what an
introspection prompt says, how to know the user is busy — live behind the
adapters (:class:`~feltstate.companion.presence.UserPresenceAdapter`,
:class:`~feltstate.companion.dispatch.BehaviorDispatcher`,
:class:`~feltstate.companion.topics.PendingTopicsStore`). feltstate ships the
loop, the gates, the state machine, and reference behaviours.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from ..engine import Engine
from .dispatch import BehaviorDispatcher
from .gates import SchedulerConfig, is_busy
from .presence import UserPresenceAdapter
from .topics import PendingTopicsStore

_log = logging.getLogger(__name__)


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

        Apply this behaviour's own gates here. **Do not** consume anything that a
        failed delivery would lose — a pending topic, a daily-quota slot, a
        once-per-window flag: that bookkeeping belongs in :meth:`commit`, which
        the scheduler calls only *after* the dispatcher confirms the payload was
        delivered. ``propose`` may still write pure *observation* state (e.g. a
        focus-duration clock that tracks the world regardless of whether this
        tick fires). Namespaced keys must not collide across kinds.
        """
        ...

    def commit(self, state: dict, now: datetime, cfg: SchedulerConfig) -> None:
        """Persist the consuming side effects of a *delivered* fire.

        Called by the scheduler (with the same ``state``/``now``/``cfg`` handed to
        :meth:`propose`) only after :meth:`dispatch` returns ``True`` for the
        payload this source proposed on the same tick. This is where a topic is
        marked consumed, a quota slot is spent, or a once-per-window flag is set —
        so a failed delivery leaves all of it untouched and the behaviour can fire
        again next tick. Default: nothing to commit.
        """
        return None


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
        lock: threading.RLock | None = None,
    ) -> None:
        # Shared with the owning Companion so a foreground say() and this
        # heartbeat's idle eng.tick([]) serialise on ONE lock. Without it both
        # paths drive Engine.save() through the same state.json.tmp and one
        # replace() races the other into a FileNotFoundError. Standalone use
        # (no Companion) gets its own lock.
        self._lock = lock or threading.RLock()
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
        self._lifecycle_lock = threading.Lock()

    @property
    def state(self) -> dict:
        """The live scheduler state dict (namespaced keys)."""
        return self._state

    # -- state persistence ------------------------------------------------- #
    @staticmethod
    def _default_state() -> dict:
        return {
            "boot_ts": 0.0,
            "today_date": "",
            "today_count": 0,
            "last_trigger_ts": 0.0,
        }

    def _load_state(self) -> dict:
        if not self.state_path.exists():
            return self._default_state()
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("scheduler state root must be a JSON object")
            return data
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
            quarantined = self._quarantine_corrupt_state()
            where = (
                f"quarantined to {quarantined.name}"
                if quarantined is not None
                else "could not be quarantined (left in place)"
            )
            _log.warning(
                "scheduler: state file %s corrupt/unreadable (%s); %s; starting from defaults",
                self.state_path,
                exc,
                where,
            )
            return self._default_state()

    def _quarantine_corrupt_state(self) -> Path | None:
        """Move a broken scheduler-state file aside for diagnosis/recovery."""
        try:
            stamp = int(time.time())
            dest = self.state_path.with_name(f"{self.state_path.name}.corrupt-{stamp}")
            n = 1
            while dest.exists():
                dest = self.state_path.with_name(f"{self.state_path.name}.corrupt-{stamp}.{n}")
                n += 1
            self.state_path.replace(dest)
            return dest
        except OSError:
            return None

    def _save_state(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
            tmp.write_text(json.dumps(self._state, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.state_path)
        except OSError as exc:
            _log.warning("scheduler: could not persist state to %s: %s", self.state_path, exc)

    def _reset_count_if_new_day(self, now: datetime) -> None:
        today = now.strftime("%Y-%m-%d")
        if self._state.get("today_date") != today:
            self._state["today_date"] = today
            self._state["today_count"] = 0

    # -- the tick ---------------------------------------------------------- #
    def tick_once(self, now: datetime | None = None) -> str | None:
        """One scheduler iteration. Returns the kind that fired, or ``None``.

        Holds the shared Companion lock for the whole iteration so the idle
        ``eng.tick([])`` and any dispatched proactive turn cannot race a
        foreground ``say()`` on Engine state or the shared ``state.json.tmp``.
        """
        with self._lock:
            return self._tick_once_locked(now)

    def _tick_once_locked(self, now: datetime | None = None) -> str | None:
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
            except Exception as exc:
                _log.warning("scheduler: %s.propose() raised: %s", src.kind, exc)
                continue
            if payload is None:
                continue
            try:
                delivered = self.dispatcher.dispatch(src.kind, payload)
            except Exception as exc:
                _log.warning("scheduler: dispatcher raised for kind=%s: %s", src.kind, exc)
                delivered = False
            if delivered:
                # Consume state (topic / quota / window flag) only now that the
                # dispatcher has confirmed delivery, then stop for this heartbeat:
                # one tick fires at most one source, and a failed delivery above
                # leaves the topic pending and the quota unspent.
                try:
                    src.commit(st, now, self.cfg)
                except Exception as exc:
                    _log.warning("scheduler: %s.commit() raised: %s", src.kind, exc)
                self._save_state()
                return src.kind

        self._save_state()
        return None

    # -- lifecycle --------------------------------------------------------- #
    def start(self) -> None:
        """Spawn the daemon heartbeat thread (idempotent).

        If a previous ``stop()`` timed out because an adapter was blocked, its
        thread handle remains live and this method deliberately refuses to spawn
        a duplicate heartbeat. Once that original thread exits, a later call may
        start a fresh one.
        """
        with self._lifecycle_lock:
            if self._thread is not None:
                if self._thread.is_alive():
                    return
                self._thread = None
                self._stop = None
            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._loop,
                args=(stop_event,),
                daemon=True,
                name="feltstate-companion-scheduler",
            )
            self._stop = stop_event
            self._thread = thread
            thread.start()

    def _loop(self, stop_event: threading.Event) -> None:
        # The event is passed by value to this particular thread. Never read
        # ``self._stop`` here: a later restart must not replace the old thread's
        # stop signal and accidentally revive it.
        while not stop_event.is_set():
            try:
                self.tick_once()
            except Exception as exc:
                _log.warning("scheduler: tick_once() raised unexpectedly: %s", exc)
                # a bad tick must never kill the heartbeat
            stop_event.wait(self.cfg.tick_interval_s)

    def stop(self, timeout: float = 2.0) -> bool:
        """Signal the heartbeat thread to stop and wait up to ``timeout`` seconds.

        Returns ``True`` if no heartbeat remains. If an adapter is still blocking
        after the timeout, returns ``False`` and retains the original thread/event
        references so :meth:`start` cannot create a second scheduler beside it.
        """
        with self._lifecycle_lock:
            thread = self._thread
            stop_event = self._stop
            if thread is None:
                return True
            if stop_event is not None:
                stop_event.set()

        if thread is threading.current_thread():
            _log.warning("scheduler: stop() called from heartbeat thread; cannot join itself")
            return False

        thread.join(timeout=max(0.0, float(timeout)))

        with self._lifecycle_lock:
            alive = thread.is_alive()
            if self._thread is thread and not alive:
                self._thread = None
                self._stop = None
            if alive:
                _log.warning(
                    "scheduler: heartbeat did not stop within %.3fs; "
                    "duplicate start is suppressed until it exits",
                    max(0.0, float(timeout)),
                )
            return not alive
