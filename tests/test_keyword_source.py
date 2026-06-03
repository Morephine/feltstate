"""KeywordSource — rule-based affect reading: cues, aggregation, mixed feelings,
label hygiene, and reading the user (not the agent)."""

from feltstate.config import DEFAULT_LABELS
from feltstate.sources.keyword import KeywordSource
from feltstate.state import AffectState

SRC = KeywordSource()
_VALID = set(DEFAULT_LABELS)


def _read(text):
    return SRC.read([{"role": "user", "content": text}], baseline=AffectState())


def test_empty_text_is_neutral_low_confidence():
    d = SRC.read([{"role": "user", "content": "   "}], baseline=AffectState())
    assert d.labels == [] and d.valence == 0.0
    assert d.confidence == SRC.neutral_confidence


def test_positive_cue_reads_positive():
    d = _read("thank you so much, this means a lot")
    assert "grateful" in d.labels
    assert d.valence > 0.3
    assert d.confidence > SRC.neutral_confidence


def test_negative_cue_reads_negative():
    d = _read("ugh this is so frustrating, it keeps crashing")
    assert "frustrated" in d.labels
    assert d.valence < 0.0


def test_unrecognised_text_is_neutral():
    d = _read("the wooden table stands in the corner")
    assert d.labels == ["neutral"]
    assert d.confidence == SRC.neutral_confidence


def test_apology_is_a_mixed_feeling():
    d = _read("i'm sorry, my bad")
    assert d.mixed_blend is not None
    assert d.mixed_blend["primary"] in _VALID
    assert d.mixed_blend["secondary"] in _VALID


def test_only_emits_known_labels_capped_at_three():
    d = _read("i'm so happy and grateful but also a little tired and anxious")
    assert set(d.labels) <= _VALID
    assert 1 <= len(d.labels) <= 3


def test_reads_latest_user_turn_not_the_agent():
    # The agent's own (sad-sounding) line must not be what gets read.
    msgs = [
        {"role": "user", "content": "thank you, that's wonderful"},
        {"role": "assistant", "content": "i feel so sad and broken"},
    ]
    d = SRC.read(msgs, baseline=AffectState())
    assert "grateful" in d.labels and d.valence > 0.0


def test_ranges_are_clamped():
    d = _read("yay amazing awesome fantastic so happy thrilled delighted!!!")
    assert -1.0 <= d.valence <= 1.0
    assert 0.0 <= d.arousal <= 1.0
    assert 0.0 <= d.confidence <= 0.85  # a keyword scanner never claims certainty
