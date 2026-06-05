"""Tests for one-turn orchestration (companion.round)."""

from __future__ import annotations

from feltstate import Engine, KeywordSource
from feltstate.companion import EchoBackend, companion_turn, extract_emotion_tag


def _eng(tmp_path) -> Engine:
    return Engine(source=KeywordSource(), state_path=str(tmp_path / "state.json"))


def test_extract_emotion_tag():
    assert extract_emotion_tag("[joy] hey there") == "joy"
    assert extract_emotion_tag("plain text, no tag") is None
    assert extract_emotion_tag("[Smile] lowercased") == "smile"
    assert extract_emotion_tag("") is None


def test_turn_injects_felt_block_and_replies(tmp_path):
    eng = _eng(tmp_path)
    history: list[dict] = []
    result = companion_turn(
        eng, EchoBackend(), history, "I finally shipped it!", system_prompt="SYS"
    )
    # EchoBackend echoes the user content it saw — which is the injected turn,
    # so the reply reflects the user's words and the felt block carried them.
    assert "echo" in result.reply.lower()
    assert "I finally shipped it!" in result.felt_block
    # history now has the user turn then the assistant reply.
    assert history[0]["role"] == "user"
    assert history[-1]["role"] == "assistant"
    assert history[-1]["content"] == result.reply


def test_emotion_label_prefers_reply_tag(tmp_path):
    eng = _eng(tmp_path)

    class TaggedBackend(EchoBackend):
        def complete(self, messages: list[dict]) -> str:
            return "[grateful] thank you"

    result = companion_turn(eng, TaggedBackend(), [], "hi", system_prompt="SYS")
    assert result.emotion_label == "grateful"


def test_skip_tick_leaves_state_unchanged(tmp_path):
    eng = _eng(tmp_path)
    eng.tick([{"role": "user", "content": "I'm so happy, thank you!"}])
    valence_before = eng.state.mood.valence
    companion_turn(
        eng,
        EchoBackend(),
        [],
        "a proactive line",
        system_prompt="SYS",
        skip_tick=True,
        skip_history=True,
    )
    # No tick happened, so the felt state did not move from the injected text.
    assert eng.state.mood.valence == valence_before


def test_system_prompt_is_first_message(tmp_path):
    eng = _eng(tmp_path)

    class CapturingBackend(EchoBackend):
        seen: list[dict] = []

        def complete(self, messages: list[dict]) -> str:
            CapturingBackend.seen = messages
            return "ok"

    companion_turn(eng, CapturingBackend(), [], "hello", system_prompt="THE-SYSTEM")
    assert CapturingBackend.seen[0]["role"] == "system"
    assert CapturingBackend.seen[0]["content"] == "THE-SYSTEM"
    assert CapturingBackend.seen[-1]["role"] == "user"
