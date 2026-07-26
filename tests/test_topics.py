"""Tests for the pending-topics queue (JsonlTopicsStore)."""

from __future__ import annotations

from feltstate.companion import JsonlTopicsStore


def test_empty_store_returns_none(tmp_path):
    store = JsonlTopicsStore(tmp_path / "topics.jsonl")
    assert store.read_oldest_unconsumed() is None


def test_append_read_oldest_first(tmp_path):
    store = JsonlTopicsStore(tmp_path / "topics.jsonl")
    store.append("ask about the deploy")
    store.append("mention the cat")
    assert store.read_oldest_unconsumed() == "ask about the deploy"


def test_mark_consumed_advances(tmp_path):
    store = JsonlTopicsStore(tmp_path / "topics.jsonl")
    store.append("first")
    store.append("second")
    store.mark_consumed("first")
    assert store.read_oldest_unconsumed() == "second"
    store.mark_consumed("second")
    assert store.read_oldest_unconsumed() is None


def test_mark_consumed_is_idempotent(tmp_path):
    store = JsonlTopicsStore(tmp_path / "topics.jsonl")
    store.append("x")
    store.mark_consumed("x")
    store.mark_consumed("x")  # second time is a no-op, must not raise
    assert store.read_oldest_unconsumed() is None


def test_persists_across_instances(tmp_path):
    path = tmp_path / "topics.jsonl"
    JsonlTopicsStore(path).append("survive a restart")
    assert JsonlTopicsStore(path).read_oldest_unconsumed() == "survive a restart"


def test_mark_consumed_is_atomic_and_keeps_queue(tmp_path):
    # v0.2.1: consume rewrites via tmp+replace under one shared lock —
    # the rest of the queue must survive intact, no tmp litter left behind.
    from feltstate.companion.topics import JsonlTopicsStore

    s = JsonlTopicsStore(tmp_path / "topics.jsonl")
    s.append("ask about the deploy")
    s.append("ask about the trip")
    s.mark_consumed("ask about the deploy")
    assert s.read_oldest_unconsumed() == "ask about the trip"
    recs = s._read()
    assert len(recs) == 2 and recs[0]["consumed"] is True
    assert not list(tmp_path.glob("*.tmp"))


def test_scheduler_naive_now_is_normalised(tmp_path):
    """v0.2.1 clock unification: a naive caller datetime must not crash or skew.

    This used to assert stdlib truisms — that ``datetime.now()`` is naive and
    ``.astimezone()`` makes it aware — without touching the scheduler at all.
    Deleting the normalisation it names (``scheduler.py``, ``tick_once``) left
    the whole suite green. It now drives the real code: a naive ``now`` must
    produce the same decision, and the same stored timestamps, as the aware
    instant it denotes.
    """
    import random
    from datetime import datetime, timedelta

    from feltstate import Engine
    from feltstate.companion.scheduler import CompanionScheduler, SchedulerConfig
    from feltstate.companion.sources_ref import RandomSource
    from feltstate.sources.keyword import KeywordSource

    class _Idle:
        def is_idle(self) -> bool:
            return True

        def seconds_since_last_user_message(self) -> float:
            return float("inf")

    class _Rec:
        def __init__(self):
            self.said = []

        def say(self, text, **_):
            self.said.append(text)
            return text

    def _make(tag: str) -> tuple[CompanionScheduler, _Rec]:
        rec = _Rec()
        sch = CompanionScheduler(
            Engine(source=KeywordSource(), state_path=str(tmp_path / f"state_{tag}.json")),
            presence=_Idle(),
            dispatcher=rec,
            sources=[RandomSource(["hi"], probability=1.0, rng=random.Random(0), kind="random")],
            state_path=str(tmp_path / f"sch_{tag}.json"),
            cfg=SchedulerConfig(boot_grace_s=0),
        )
        return sch, rec

    naive = datetime.now().replace(microsecond=0) - timedelta(hours=1)
    aware = naive.astimezone()
    assert naive.tzinfo is None and aware.tzinfo is not None

    naive_sch, naive_rec = _make("naive")
    aware_sch, aware_rec = _make("aware")
    naive_sch.tick_once(now=naive)
    aware_sch.tick_once(now=aware)

    # Same instant, same decision, same bookkeeping — the naive input was
    # normalised rather than compared against an aware value or stored raw.
    assert naive_rec.said == aware_rec.said
    assert naive_sch._state.get("boot_ts") == aware_sch._state.get("boot_ts")
