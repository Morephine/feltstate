"""FactExtractor — propose 5W1H facts via a second model pass (HTTP stubbed),
plus committing them to a Canon. Mirrors the affect-source contract: separate
pass, never raises."""

from feltstate.memory import Canon, LLMFactExtractor, commit_to_canon


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
