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


# --------------------------------------------------------------------------- #
# Additional edge cases and invariants                                        #
# --------------------------------------------------------------------------- #
def test_n_total_is_length_of_turns_list():
    """n_total must always equal len(turns), regardless of the window."""
    turns = _turns()
    r = get_turn_context(turns, 2, before=1, after=1)
    assert r["ok"] and r["n_total"] == len(turns)


def test_after_available_at_last_index_is_zero():
    """When anchored at the last turn, nothing remains after it."""
    turns = _turns()
    r = get_turn_context(turns, len(turns) - 1, before=0, after=0)
    assert r["ok"] and r["after_available"] == 0


def test_after_available_at_first_index():
    """When anchored at the first turn, all other turns are 'after'."""
    turns = _turns()
    r = get_turn_context(turns, 0, before=0, after=0)
    assert r["ok"] and r["after_available"] == len(turns) - 1


def test_single_turn_list_exact_match():
    """A list of one turn anchored at its timestamp returns just that turn."""
    turns = [{"role": "human", "content": "only", "timestamp": "2026-01-01T12:00:00"}]
    r = get_turn_context(turns, "2026-01-01T12:00", before=5, after=5)
    assert r["ok"] and not r["approx"]
    assert [t["content"] for t in r["turns"]] == ["only"]
    assert r["n_total"] == 1 and r["after_available"] == 0


def test_approx_false_on_exact_minute_match():
    """Even when a fallback could apply, an exact-minute hit must NOT be marked approx."""
    r = get_turn_context(_turns(), "2026-06-06T10:00:30")  # second part ignored for minute match
    # 10:00:30 has the same minute prefix as the first turn at 10:00:00
    assert r["ok"] and r["match_index"] == 0 and not r["approx"]


def test_before_zero_after_zero_returns_exactly_anchor():
    """before=0, after=0 must return exactly the anchor turn and nothing else."""
    r = get_turn_context(_turns(), 3, before=0, after=0)
    assert r["ok"] and len(r["turns"]) == 1 and r["turns"][0]["content"] == "d"


def test_load_turns_non_list_json_returns_empty(tmp_path):
    """A JSON file that contains a dict (not a list) is treated as unreadable."""
    import json

    f = tmp_path / "bad.json"
    f.write_text(json.dumps({"role": "human", "content": "x"}), encoding="utf-8")
    assert load_turns(f) == []


def test_load_turns_invalid_json_returns_empty(tmp_path):
    """A file with invalid JSON is silently ignored."""
    f = tmp_path / "broken.json"
    f.write_text("not json {{{", encoding="utf-8")
    assert load_turns(f) == []


def test_load_turns_role_filter(tmp_path):
    """Only dicts whose ``role`` matches ``roles`` are returned."""
    import json

    turns = [
        {"role": "user", "content": "hi"},
        {"role": "system", "content": "you are an AI"},
        {"role": "assistant", "content": "hello"},
    ]
    f = tmp_path / "chat.json"
    f.write_text(json.dumps(turns), encoding="utf-8")
    # Default roles: user/assistant/human/ai — system is excluded.
    loaded = load_turns(f)
    roles_seen = {t["role"] for t in loaded}
    assert "system" not in roles_seen
    assert "user" in roles_seen and "assistant" in roles_seen


def test_load_turns_custom_roles(tmp_path):
    """Passing a custom ``roles`` tuple restricts which turns are returned."""
    import json

    turns = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    f = tmp_path / "chat.json"
    f.write_text(json.dumps(turns), encoding="utf-8")
    loaded = load_turns(f, roles=("assistant",))
    assert len(loaded) == 1 and loaded[0]["role"] == "assistant"


def test_range_context_includes_entire_source_span_and_surroundings():
    from feltstate.memory.context import get_turn_range_context

    r = get_turn_range_context(
        _turns(),
        "2026-06-06T10:01:00",
        "2026-06-06T10:03:00",
        before=1,
        after=1,
    )
    assert r["ok"] and not r["approx"]
    assert r["source_start_index"] == 1
    assert r["source_end_index"] == 3
    assert [t["content"] for t in r["source_turns"]] == ["b", "c", "d"]
    assert [t["content"] for t in r["turns"]] == ["a", "b", "c", "d", "e"]


def test_range_context_falls_back_to_start_when_no_turn_is_inside():
    from feltstate.memory.context import get_turn_range_context

    r = get_turn_range_context(
        _turns(),
        "2026-06-06T10:09:00",
        "2026-06-06T10:10:00",
        before=0,
        after=0,
    )
    assert r["ok"] and r["approx"] and r["range_fallback"]
    assert r["source_start_index"] == r["source_end_index"] == 4


def test_range_context_rejects_reversed_range():
    from feltstate.memory.context import get_turn_range_context

    r = get_turn_range_context(_turns(), "2026-06-06T10:03:00", "2026-06-06T10:01:00")
    assert r == {"ok": False, "reason": "range end precedes start"}
