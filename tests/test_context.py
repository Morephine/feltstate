"""Tests for memory.context — expanding a fact back to its surrounding turns."""

from __future__ import annotations

from feltstate.memory.context import get_turn_context, load_turns


def _turns() -> list[dict]:
    return [
        {"role": "human", "content": "a", "timestamp": "2026-06-06T10:00:00"},
        {"role": "ai", "content": "b", "timestamp": "2026-06-06T10:01:00"},
        {"role": "human", "content": "c", "timestamp": "2026-06-06T10:02:00"},
        {"role": "ai", "content": "d", "timestamp": "2026-06-06T10:03:00"},
        {"role": "human", "content": "e", "timestamp": "2026-06-06T10:04:00"},
    ]


def test_exact_minute_match():
    r = get_turn_context(_turns(), "2026-06-06T10:02", before=1, after=1)
    assert r["ok"] and r["match_index"] == 2 and not r["approx"]
    assert [t["content"] for t in r["turns"]] == ["b", "c", "d"]


def test_chat_prefix_is_stripped():
    r = get_turn_context(_turns(), "chat:2026-06-06T10:02", before=0, after=0)
    assert r["ok"] and [t["content"] for t in r["turns"]] == ["c"]


def test_index_anchor():
    r = get_turn_context(_turns(), 0, before=2, after=2)
    assert r["ok"] and [t["content"] for t in r["turns"]] == ["a", "b", "c"]


def test_fallback_to_latest_before_anchor():
    # 10:09 has no exact-minute turn -> falls back to the latest turn <= it (10:04).
    r = get_turn_context(_turns(), "2026-06-06T10:09", before=1, after=1)
    assert r["ok"] and r["approx"] and r["match_index"] == 4


def test_before_after_clamp_to_bounds():
    r = get_turn_context(_turns(), 2, before=5, after=5)
    assert len(r["turns"]) == 5  # ±5 clamps to the whole 5-turn list


def test_after_available_reported():
    r = get_turn_context(_turns(), 1, before=0, after=0)
    assert r["after_available"] == 3  # turns 2,3,4 remain after index 1


def test_bad_inputs():
    assert get_turn_context([], "x")["ok"] is False
    assert get_turn_context(_turns(), "")["ok"] is False
    assert get_turn_context(_turns(), 99)["ok"] is False
    assert get_turn_context(_turns(), "2026-06-05T10:00")["ok"] is False  # before all turns


def test_load_turns(tmp_path):
    import json

    f = tmp_path / "chat.json"
    f.write_text(
        json.dumps(
            [
                {"role": "meta", "version": 1},
                {"role": "human", "content": "x", "timestamp": "t1"},
                {"role": "ai", "content": "y", "timestamp": "t2"},
            ]
        ),
        encoding="utf-8",
    )
    turns = load_turns(f)
    assert [t["content"] for t in turns] == ["x", "y"]  # metadata row dropped
    assert load_turns(tmp_path / "missing.json") == []  # missing file -> []
