"""Tests for the proactive heartbeat (companion.scheduler) with a fake clock."""

from __future__ import annotations

import logging
import random
import threading
from datetime import datetime, timedelta
from unittest.mock import patch

from feltstate import Engine, KeywordSource
from feltstate.companion import (
    AlwaysIdlePresence,
    BehaviorDispatcher,
    BehaviorSource,
    CompanionScheduler,
    JsonlTopicsStore,
    PendingTopicsSource,
    RandomSource,
    SchedulerConfig,
    UserPresenceAdapter,
)

T0 = datetime(2026, 6, 5, 10, 0, 0)


class RecDispatcher(BehaviorDispatcher):
    def __init__(self) -> None:
        self.fired: list[tuple[str, str]] = []

    def dispatch(self, kind: str, payload: str) -> bool:
        self.fired.append((kind, payload))
        return True


class FailingDispatcher(BehaviorDispatcher):
    """Records the attempt but reports the delivery failed (returns False)."""

    def __init__(self) -> None:
        self.attempts: list[tuple[str, str]] = []

    def dispatch(self, kind: str, payload: str) -> bool:
        self.attempts.append((kind, payload))
        return False


class RaisingDispatcher(BehaviorDispatcher):
    """A dispatcher that blows up on delivery (must be caught by the scheduler)."""

    def __init__(self) -> None:
        self.attempts: list[tuple[str, str]] = []

    def dispatch(self, kind: str, payload: str) -> bool:
        self.attempts.append((kind, payload))
        raise RuntimeError("delivery pipeline is down")


class BusyPresence(UserPresenceAdapter):
    def is_busy(self) -> bool:
        return True

    def seconds_since_last_user_message(self) -> float:
        return float("inf")


def _eng(tmp_path) -> Engine:
    return Engine(source=KeywordSource(), state_path=str(tmp_path / "state.json"))


def _sch(tmp_path, sources, *, presence=None, **cfgkw) -> CompanionScheduler:
    return CompanionScheduler(
        _eng(tmp_path),
        presence=presence or AlwaysIdlePresence(),
        dispatcher=RecDispatcher(),
        sources=sources,
        state_path=str(tmp_path / "sch.json"),
        cfg=SchedulerConfig(boot_grace_s=0, **cfgkw),
    )


def _always(kind="random", priority=3, seed=0):
    return RandomSource(
        ["hi"], probability=1.0, rng=random.Random(seed), kind=kind, priority=priority
    )


def test_fires_when_eligible(tmp_path):
    sch = _sch(tmp_path, [_always()])
    assert sch.tick_once(now=T0) == "random"
    assert sch.dispatcher.fired == [("random", "hi")]  # type: ignore[attr-defined]


def test_min_gap_blocks_second_then_allows(tmp_path):
    sch = _sch(tmp_path, [_always()], min_gap_s=1800)
    assert sch.tick_once(now=T0) == "random"
    assert sch.tick_once(now=T0 + timedelta(seconds=60)) is None
    assert sch.tick_once(now=T0 + timedelta(seconds=2000)) == "random"


def test_daily_max_exhausts(tmp_path):
    sch = _sch(tmp_path, [_always()], min_gap_s=0, daily_max=2)
    assert sch.tick_once(now=T0) == "random"
    assert sch.tick_once(now=T0 + timedelta(seconds=10)) == "random"
    assert sch.tick_once(now=T0 + timedelta(seconds=20)) is None


def test_busy_blocks_everything(tmp_path):
    sch = _sch(tmp_path, [_always()], presence=BusyPresence())
    assert sch.tick_once(now=T0) is None
    assert sch.dispatcher.fired == []  # type: ignore[attr-defined]


def test_priority_order_highest_first(tmp_path):
    lo = _always(kind="lo", priority=3, seed=1)
    hi = _always(kind="hi", priority=0, seed=2)
    sch = _sch(tmp_path, [lo, hi], min_gap_s=0)  # pass in reversed order
    assert sch.tick_once(now=T0) == "hi"


def test_state_persists_across_instances(tmp_path):
    sch = _sch(tmp_path, [_always()], min_gap_s=1800)
    assert sch.tick_once(now=T0) == "random"
    # A fresh scheduler reading the same state file still sees the gap.
    sch2 = _sch(tmp_path, [_always()], min_gap_s=1800)
    assert sch2.tick_once(now=T0 + timedelta(seconds=60)) is None


# --- delivery-failure safety (#43/#44/#45): consume state only after success -- #


def _sch_with(tmp_path, sources, dispatcher, *, presence=None, **cfgkw):
    return CompanionScheduler(
        _eng(tmp_path),
        presence=presence or AlwaysIdlePresence(),
        dispatcher=dispatcher,
        sources=sources,
        state_path=str(tmp_path / "sch.json"),
        cfg=SchedulerConfig(boot_grace_s=0, **cfgkw),
    )


def test_failed_delivery_leaves_topic_pending_and_quota_unspent(tmp_path):
    # A pending topic + a dispatcher that reports failure: the topic must stay
    # unconsumed (raise-able again) and the daily quota must not be spent.
    topics = JsonlTopicsStore(tmp_path / "topics.jsonl")
    topics.append("ask how the deploy went")
    disp = FailingDispatcher()
    sch = _sch_with(tmp_path, [PendingTopicsSource(topics)], disp, min_gap_s=0)

    assert sch.tick_once(now=T0) is None  # nothing was delivered
    assert disp.attempts == [("pending", "ask how the deploy went")]  # it was tried
    # The topic is still pending — a failed send did not burn the note.
    assert topics.read_oldest_unconsumed() == "ask how the deploy went"
    # And no quota was spent, so the count is still zero.
    assert int(sch.state.get("today_count", 0)) == 0


def test_raising_dispatcher_leaves_topic_pending(tmp_path):
    # Same guarantee when the dispatcher raises instead of returning False.
    topics = JsonlTopicsStore(tmp_path / "topics.jsonl")
    topics.append("mention the cat")
    disp = RaisingDispatcher()
    sch = _sch_with(tmp_path, [PendingTopicsSource(topics)], disp, min_gap_s=0)

    assert sch.tick_once(now=T0) is None  # the raise was caught, nothing fired
    assert topics.read_oldest_unconsumed() == "mention the cat"
    assert int(sch.state.get("today_count", 0)) == 0


def test_topic_consumed_and_quota_spent_only_on_success(tmp_path):
    # The positive counterpart: on a successful delivery the topic IS consumed
    # and the quota IS spent (so the fix didn't just disable consumption).
    topics = JsonlTopicsStore(tmp_path / "topics.jsonl")
    topics.append("the one topic")
    sch = _sch_with(tmp_path, [PendingTopicsSource(topics)], RecDispatcher(), min_gap_s=0)

    assert sch.tick_once(now=T0) == "pending"
    assert topics.read_oldest_unconsumed() is None  # consumed
    assert int(sch.state.get("today_count", 0)) == 1  # quota spent


def test_failed_delivery_retries_next_tick(tmp_path):
    # End to end: a fail then a success on the same topic. Because the first
    # failure consumed nothing, the second tick still finds and delivers it.
    topics = JsonlTopicsStore(tmp_path / "topics.jsonl")
    topics.append("survive a failed send")

    fail = _sch_with(tmp_path, [PendingTopicsSource(topics)], FailingDispatcher(), min_gap_s=0)
    assert fail.tick_once(now=T0) is None
    # A fresh scheduler over the same files (simulating a later heartbeat) delivers.
    ok = _sch_with(tmp_path, [PendingTopicsSource(topics)], RecDispatcher(), min_gap_s=0)
    assert ok.tick_once(now=T0 + timedelta(seconds=60)) == "pending"
    assert topics.read_oldest_unconsumed() is None


def test_only_first_successful_source_fires_per_tick(tmp_path):
    # Two eligible sources, both would fire; only the higher-priority one is
    # dispatched and only its quota is spent (one heartbeat, one fire).
    hi = _always(kind="hi", priority=0, seed=1)
    lo = _always(kind="lo", priority=3, seed=2)
    disp = RecDispatcher()
    sch = _sch_with(tmp_path, [lo, hi], disp, min_gap_s=0)

    assert sch.tick_once(now=T0) == "hi"
    assert disp.fired == [("hi", "hi")]  # the low-priority one never dispatched
    assert int(sch.state.get("today_count", 0)) == 1  # exactly one quota slot spent


# --- P1-3: atomic write (no truncated JSON on crash) ------------------------- #


def test_save_state_is_atomic_via_tmp(tmp_path):
    """_save_state must write to a .tmp sibling and then replace the target,
    so a crash mid-write leaves the old file intact instead of a truncated one."""
    sch = _sch(tmp_path, [])
    state_file = sch.state_path
    tmp_path_obj = state_file.with_suffix(state_file.suffix + ".tmp")

    # Trigger a save and check the final file is the state_path (not .tmp).
    sch.tick_once(now=T0)
    assert state_file.exists(), "state file must exist after tick"
    assert not tmp_path_obj.exists(), ".tmp sibling must be renamed away after successful write"

    # Verify the written JSON is valid (not truncated).
    import json

    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert "last_tick_ts" in data


def test_save_state_tmp_replaces_old_on_success(tmp_path):
    """If the state file already exists, a successful save replaces it
    atomically — the old content is not visible in a torn/partial state."""
    import json

    sch = _sch(tmp_path, [])
    sch.tick_once(now=T0)  # first write
    first_ts = json.loads(sch.state_path.read_text())["last_tick_ts"]

    sch.tick_once(now=T0 + timedelta(seconds=30))  # second write
    second_ts = json.loads(sch.state_path.read_text())["last_tick_ts"]

    assert second_ts != first_ts, "second tick must update the persisted timestamp"


# --- P1-3: swallowed exceptions replaced with warnings ----------------------- #


def test_propose_exception_logs_warning(tmp_path, caplog):
    """A BehaviorSource.propose() that raises must log a warning (not silently
    continue) so the fault is diagnosable."""

    class BoomSource(BehaviorSource):
        kind = "boom"
        priority = 0

        def propose(self, state, now, presence, cfg):
            raise RuntimeError("boom in propose")

    with caplog.at_level(logging.WARNING, logger="feltstate.companion.scheduler"):
        sch = _sch(tmp_path, [BoomSource()])
        sch.tick_once(now=T0)

    assert any("boom" in r.message for r in caplog.records), (
        "a propose() failure must produce a warning log"
    )


def test_dispatcher_exception_logs_warning(tmp_path, caplog):
    """A dispatcher that raises must log a warning so the fault is diagnosable."""
    sch = _sch_with(tmp_path, [_always()], RaisingDispatcher(), min_gap_s=0)

    with caplog.at_level(logging.WARNING, logger="feltstate.companion.scheduler"):
        sch.tick_once(now=T0)

    assert any(
        "dispatcher" in r.message.lower() or "delivery pipeline" in r.message
        for r in caplog.records
    ), "a dispatcher raise must produce a warning log"


def test_save_failure_logs_warning(tmp_path, caplog):
    """An OSError from _save_state must surface as a warning, not disappear."""
    sch = _sch(tmp_path, [])

    from pathlib import Path

    real_write_text = Path.write_text

    def broken_write_text(self, *args, **kwargs):
        if ".tmp" in str(self):
            raise OSError("disk full")
        return real_write_text(self, *args, **kwargs)

    with caplog.at_level(logging.WARNING, logger="feltstate.companion.scheduler"):
        with patch.object(Path, "write_text", broken_write_text):
            # _save_state must catch the OSError, log a warning, and not propagate.
            sch._save_state()  # must NOT raise

    assert any("could not persist" in r.message for r in caplog.records), (
        "an OSError in _save_state must produce a warning log"
    )


# --- P1-3: corrupt state file falls back to defaults with a warning ---------- #


def test_corrupt_state_file_falls_back_to_defaults(tmp_path, caplog):
    """Invalid JSON is quarantined, logged, and replaced with safe defaults."""

    state_file = tmp_path / "sch.json"
    state_file.write_text("{not valid json", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="feltstate.companion.scheduler"):
        sch = CompanionScheduler(
            _eng(tmp_path),
            presence=AlwaysIdlePresence(),
            dispatcher=RecDispatcher(),
            sources=[],
            state_path=str(state_file),
            cfg=SchedulerConfig(boot_grace_s=0),
        )

    assert any("corrupt/unreadable" in r.message for r in caplog.records)
    assert not state_file.exists()
    quarantined = list(tmp_path.glob("sch.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "{not valid json"
    assert isinstance(sch.state, dict)
    assert sch.state.get("today_count", 0) == 0


def test_scheduler_state_root_must_be_object(tmp_path, caplog):
    """Valid JSON with the wrong root type is schema corruption, not a state."""
    state_file = tmp_path / "sch.json"
    state_file.write_text("[]", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="feltstate.companion.scheduler"):
        sch = CompanionScheduler(
            _eng(tmp_path),
            presence=AlwaysIdlePresence(),
            dispatcher=RecDispatcher(),
            sources=[],
            state_path=state_file,
            cfg=SchedulerConfig(boot_grace_s=0),
        )

    assert isinstance(sch.state, dict)
    assert not state_file.exists()
    quarantined = list(tmp_path.glob("sch.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "[]"
    assert any("JSON object" in r.message for r in caplog.records)


def test_stop_timeout_does_not_allow_duplicate_scheduler_thread(tmp_path):
    """A blocked adapter may outlive stop's timeout, but restart must not spawn
    a second heartbeat or replace the old thread's stop event."""

    class BlockingDispatcher(BehaviorDispatcher):
        def __init__(self) -> None:
            self.entered = threading.Event()
            self.release = threading.Event()
            self.calls = 0

        def dispatch(self, kind: str, payload: str) -> bool:
            self.calls += 1
            self.entered.set()
            self.release.wait(timeout=5)
            return True

    dispatcher = BlockingDispatcher()
    sch = CompanionScheduler(
        _eng(tmp_path),
        presence=AlwaysIdlePresence(),
        dispatcher=dispatcher,
        sources=[_always()],
        state_path=tmp_path / "sch.json",
        cfg=SchedulerConfig(
            boot_grace_s=0,
            min_gap_s=0,
            tick_interval_s=0.01,
        ),
    )

    sch.start()
    assert dispatcher.entered.wait(timeout=2), "heartbeat never reached dispatcher"
    old_thread = sch._thread
    old_stop = sch._stop
    assert old_thread is not None and old_stop is not None

    assert sch.stop(timeout=0.01) is False
    assert old_thread.is_alive()

    # Restart while the old adapter is blocked must be suppressed. In particular,
    # neither the thread handle nor its already-set stop event may be replaced.
    sch.start()
    assert sch._thread is old_thread
    assert sch._stop is old_stop
    assert old_stop.is_set()

    dispatcher.release.set()
    assert sch.stop(timeout=1.0) is True
    assert not old_thread.is_alive()

    # Once the original thread is genuinely gone, starting again is allowed.
    sch.start()
    new_thread = sch._thread
    assert new_thread is not None and new_thread is not old_thread
    assert sch.stop(timeout=1.0) is True
