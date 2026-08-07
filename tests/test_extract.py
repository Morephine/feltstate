"""FactExtractor — propose 5W1H facts via a second model pass (HTTP stubbed),
plus committing them to a Canon. Mirrors the affect-source contract: separate
pass, never raises."""

import pytest

from feltstate.memory import Canon, LLMFactExtractor, commit_to_canon
from feltstate.memory.extract import (
    _clean_facts,
    _extract_content,
    _format_transcript,
    _parse_fact_array,
)


def _ext(**kw):
    return LLMFactExtractor(base_url="http://localhost:9/v1", model="m", **kw)


def _env(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def test_extracts_well_formed_facts():
    e = _ext()
    e._post = lambda chat: _env(
        '[{"actor":"Sam","object":"prefers tea","why":"said it twice","intensity":0.7}]'
    )
    facts = e.extract([{"role": "user", "content": "i always drink tea"}], actor_hint="Sam")
    assert len(facts) == 1
    f = facts[0]
    assert f["object"] == "prefers tea" and f["actor"] == "Sam" and f["intensity"] == 0.7


def test_extracts_array_even_wrapped_in_prose():
    e = _ext()
    e._post = lambda chat: _env('Sure thing: [{"object":"likes jazz"}] hope that helps')
    facts = e.extract([{"role": "user", "content": "jazz is the best"}])
    assert facts and facts[0]["object"] == "likes jazz"
    assert facts[0]["actor"] == "user"  # default actor filled in


def test_empty_transcript_skips_the_call():
    e = _ext()
    calls = {"n": 0}

    def counting(chat):
        calls["n"] += 1
        return _env("[]")

    e._post = counting
    assert e.extract([{"role": "user", "content": "   "}]) == []
    assert calls["n"] == 0


def test_failure_returns_empty_never_raises():
    e = _ext()

    def boom(chat):
        raise RuntimeError("endpoint down")

    e._post = boom
    assert e.extract([{"role": "user", "content": "hi"}]) == []


def test_malformed_body_returns_empty():
    e = _ext()
    e._post = lambda chat: _env("not json at all")
    assert e.extract([{"role": "user", "content": "hi"}]) == []


def test_clamps_intensity_and_caps_count():
    e = _ext(max_facts=2)
    e._post = lambda chat: _env('[{"object":"a","intensity":5},{"object":"b"},{"object":"c"}]')
    facts = e.extract([{"role": "user", "content": "x"}])
    assert len(facts) == 2  # capped
    assert all(0.0 <= f["intensity"] <= 1.0 for f in facts)


def test_commit_to_canon_defaults_to_grey_zone(tmp_path):
    canon = Canon(tmp_path / "canon.jsonl")
    facts = [{"actor": "user", "object": "likes hiking", "why": "mentioned", "intensity": 0.6}]
    commit_to_canon(facts, canon)  # grey zone by default
    # Not yet in the confirmed view — the agent confirms what it keeps.
    assert not any(e["object"] == "likes hiking" for e in canon.view())
    canon.confirm("hiking")
    assert any(e["object"] == "likes hiking" for e in canon.view())


def test_commit_to_canon_direct(tmp_path):
    canon = Canon(tmp_path / "canon.jsonl")
    commit_to_canon([{"object": "a core fact", "intensity": 0.9}], canon, grey_zone=False)
    assert any(e["object"] == "a core fact" for e in canon.view())


# --------------------------------------------------------------------------- #
# _format_transcript — truncation and windowing invariants                    #
# --------------------------------------------------------------------------- #
def test_format_transcript_truncates_long_content():
    """Messages longer than 800 chars are trimmed with ' ...' so the extraction
    call doesn't blow the token budget."""
    long_msg = "x" * 900
    result = _format_transcript([{"role": "user", "content": long_msg}])
    assert len(result) < 820  # truncated
    assert result.endswith(" ...")


def test_format_transcript_keeps_only_last_max_turns():
    """Only the last ``max_turns`` messages are included — the window is bounded."""
    msgs = [{"role": "user", "content": f"msg{i}"} for i in range(30)]
    result = _format_transcript(msgs, max_turns=5)
    assert "msg29" in result  # last message included
    assert "msg25" in result  # 5th-from-last included (indices 25-29)
    assert "msg24" not in result  # older than window dropped


def test_format_transcript_skips_empty_content():
    """Turns with blank or missing content are silently dropped."""
    msgs = [
        {"role": "user", "content": ""},
        {"role": "user", "content": "  "},
        {"role": "assistant", "content": "hello"},
    ]
    result = _format_transcript(msgs)
    lines = [ln for ln in result.splitlines() if ln.strip()]
    assert len(lines) == 1 and "hello" in lines[0]


def test_format_transcript_empty_list_is_empty_string():
    assert _format_transcript([]) == ""


# --------------------------------------------------------------------------- #
# _extract_content — response-shape invariants                                #
# --------------------------------------------------------------------------- #
def test_extract_content_happy_path():
    raw = {"choices": [{"message": {"content": "hello"}}]}
    assert _extract_content(raw) == "hello"


def test_extract_content_no_choices_returns_empty():
    assert _extract_content({}) == ""
    assert _extract_content({"choices": []}) == ""


def test_extract_content_missing_message_returns_empty():
    assert _extract_content({"choices": [{}]}) == ""


# --------------------------------------------------------------------------- #
# _parse_fact_array — edge-case JSON extraction                               #
# --------------------------------------------------------------------------- #
def test_parse_fact_array_empty_input():
    assert _parse_fact_array("") == []
    assert _parse_fact_array("   ") == []


def test_parse_fact_array_direct_array():
    assert _parse_fact_array('[{"object":"x"}]') == [{"object": "x"}]


def test_parse_fact_array_empty_brackets():
    assert _parse_fact_array("[]") == []


def test_parse_fact_array_non_array_json_returns_empty():
    # A model that returned a dict instead of a list
    assert _parse_fact_array('{"object":"x"}') == []


def test_parse_fact_array_extracts_from_prose_with_fences():
    text = '```json\n[{"object":"y"}]\n```'
    result = _parse_fact_array(text)
    assert result == [{"object": "y"}]


def test_parse_fact_array_no_brackets_returns_empty():
    assert _parse_fact_array("just some prose without brackets") == []


# --------------------------------------------------------------------------- #
# _clean_facts — filtering and default-filling invariants                     #
# --------------------------------------------------------------------------- #
def test_clean_facts_skips_non_dict_items():
    facts = [None, "a string", {"object": "valid"}]
    out = _clean_facts(facts, "user", 10)
    assert len(out) == 1 and out[0]["object"] == "valid"


def test_clean_facts_skips_missing_object():
    facts = [{"actor": "x", "why": "because"}, {"object": "real fact"}]
    out = _clean_facts(facts, "user", 10)
    assert len(out) == 1 and out[0]["object"] == "real fact"


def test_clean_facts_fills_default_actor():
    facts = [{"object": "something happened"}]
    out = _clean_facts(facts, "alice", 10)
    assert out[0]["actor"] == "alice"


def test_clean_facts_clamps_intensity_to_unit():
    facts = [{"object": "x", "intensity": -5}, {"object": "y", "intensity": 99}]
    out = _clean_facts(facts, "user", 10)
    assert out[0]["intensity"] == 0.0
    assert out[1]["intensity"] == 1.0


def test_clean_facts_missing_intensity_defaults_to_half():
    facts = [{"object": "no intensity here"}]
    out = _clean_facts(facts, "user", 10)
    assert out[0]["intensity"] == 0.5


# --------------------------------------------------------------------------- #
# commit_to_canon — edge cases                                                #
# --------------------------------------------------------------------------- #
def test_commit_to_canon_skips_fact_without_object(tmp_path):
    """A proposed fact with no ``object`` field must be silently dropped."""
    canon = Canon(tmp_path / "canon.jsonl")
    facts = [{"actor": "user", "why": "no object here"}]
    stored = commit_to_canon(facts, canon, grey_zone=False)
    assert stored == []
    assert canon.view() == []


def test_commit_to_canon_empty_list(tmp_path):
    canon = Canon(tmp_path / "canon.jsonl")
    assert commit_to_canon([], canon) == []
    assert commit_to_canon(None, canon) == []


def test_commit_to_canon_default_intensity_used_when_missing(tmp_path):
    """A fact without an ``intensity`` field gets the ``default_intensity`` kwarg."""
    canon = Canon(tmp_path / "canon.jsonl")
    stored = commit_to_canon([{"object": "a fact"}], canon, grey_zone=False, default_intensity=0.7)
    assert len(stored) == 1
    assert stored[0]["base_intensity"] == pytest.approx(0.7, abs=1e-3)


def test_transcript_is_numbered_and_facts_keep_cited_sources():
    """Provenance survives the extraction hop (the sources discipline).

    Turns carry ``[n]`` markers indexed against the *original* sequence, and
    ``_clean_facts`` keeps only well-formed, non-negative, deduplicated turn
    citations — a fact that cannot point back at its turns is a rumour.
    """
    from feltstate.memory.extract import _clean_facts, _format_transcript

    msgs = [{"role": "user", "content": f"turn {i}"} for i in range(30)]
    text = _format_transcript(msgs, max_turns=20)
    assert "[10] user: turn 10" in text  # offset indexing against the original list
    assert "[29] user: turn 29" in text
    assert "[9]" not in text  # outside the slice

    facts = [
        {"object": "cited", "sources": [5, 5, 3.0, -1, "junk"]},
        {"object": "uncited"},
    ]
    cleaned = _clean_facts(facts, "user", 8)
    assert cleaned[0]["sources"] == [3, 5]  # deduped, sorted, junk dropped
    assert cleaned[1]["sources"] == []


def test_commit_imprints_sources_and_birth_affect(tmp_path):
    """The commit hop: citations become durable pointers, birth affect is the
    caller's measured snapshot — never the extraction model's claim."""
    from feltstate import Canon
    from feltstate.memory.extract import commit_to_canon

    c = Canon(tmp_path / "canon.jsonl")
    facts = [{"object": "likes tea", "actor": "sam", "sources": [3], "intensity": 0.6}]

    stored = commit_to_canon(
        facts,
        c,
        grey_zone=False,
        birth_affect={"v": 0.31, "a": 0.62, "bogus": "dropped"},
        source_of=lambda n: f"chat/day.jsonl#{n}",
    )
    assert stored

    import json

    row = json.loads((tmp_path / "canon.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert row["sources"] == ["chat/day.jsonl#3"]
    assert row["birth_affect"] == {"v": 0.31, "a": 0.62}  # non-numeric keys dropped

    # Without a resolver, citations stay honest about being slice-relative.
    c2 = Canon(tmp_path / "canon2.jsonl")
    commit_to_canon(facts, c2, grey_zone=False)
    row2 = json.loads((tmp_path / "canon2.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert row2["sources"] == ["turn:3"]
    assert "birth_affect" not in row2
