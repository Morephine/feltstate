"""LLMSource — measurement via an OpenAI-compatible endpoint, with the HTTP call
stubbed so tests never touch the network. The contract under test: parse a good
measurement, sanitise labels, and *never raise* on any failure."""

from feltstate.sources.llm import LLMSource
from feltstate.state import AffectState


def _src():
    return LLMSource(base_url="http://localhost:9/v1", model="test-model")


def _envelope(content: str) -> dict:
    """An OpenAI-shaped chat-completions response carrying ``content``."""
    return {"choices": [{"message": {"content": content}}]}


def _read(src, text="thank you"):
    return src.read([{"role": "user", "content": text}], baseline=AffectState())


def test_parses_a_clean_measurement():
    src = _src()
    src._post = lambda chat: _envelope(
        '{"valence":0.6,"arousal":0.5,"labels":["grateful"],'
        '"confidence":0.8,"monologue":"that landed warm"}'
    )
    d = _read(src)
    assert d.valence == 0.6 and d.arousal == 0.5
    assert d.labels == ["grateful"] and d.confidence == 0.8
    assert d.monologue == "that landed warm"


def test_extracts_json_even_wrapped_in_prose():
    src = _src()
    src._post = lambda chat: _envelope('Sure! {"valence":-0.3,"labels":["sad"]} hope this helps')
    d = _read(src)
    assert d.valence == -0.3 and "sad" in d.labels


def test_unknown_labels_are_dropped():
    src = _src()
    src._post = lambda chat: _envelope(
        '{"valence":0.1,"labels":["grateful","banana","totally_made_up"]}'
    )
    d = _read(src)
    assert "grateful" in d.labels
    assert "banana" not in d.labels and "totally_made_up" not in d.labels


def test_network_failure_returns_neutral_never_raises():
    src = _src()

    def boom(chat):
        raise RuntimeError("endpoint down")

    src._post = boom
    d = _read(src)  # must not raise
    assert d.labels == [] and d.confidence <= 0.2


def test_malformed_body_returns_neutral():
    src = _src()
    src._post = lambda chat: _envelope("not json at all, sorry")
    d = _read(src)
    assert d.labels == [] and d.confidence <= 0.2


def test_empty_user_text_skips_the_call():
    src = _src()
    calls = {"n": 0}

    def counting(chat):
        calls["n"] += 1
        return _envelope("{}")

    src._post = counting
    # No user turn at all -> nothing to measure, no HTTP call.
    d = src.read([{"role": "assistant", "content": "hi there"}], baseline=AffectState())
    assert calls["n"] == 0 and d.confidence <= 0.2


def test_out_of_range_fields_are_clamped():
    src = _src()
    src._post = lambda chat: _envelope('{"valence":5,"arousal":-2,"confidence":9}')
    d = _read(src)
    assert -1.0 <= d.valence <= 1.0
    assert 0.0 <= d.arousal <= 1.0
    assert 0.0 <= d.confidence <= 1.0
