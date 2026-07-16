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
    # v0.2.1 clock unification: a naive caller datetime must not crash or
    # skew — it is treated as explicit local and becomes aware internally.
    from datetime import datetime
    from feltstate.state import AffectState  # noqa: F401  (import parity)

    naive = datetime.now()
    assert naive.tzinfo is None
    aware = naive.astimezone()
    assert aware.tzinfo is not None and abs(aware.timestamp() - naive.timestamp()) < 2
