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
