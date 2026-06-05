"""Tests for the proactive heartbeat (companion.scheduler) with a fake clock."""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from feltstate import Engine, KeywordSource
from feltstate.companion import (
    AlwaysIdlePresence,
    BehaviorDispatcher,
    CompanionScheduler,
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
