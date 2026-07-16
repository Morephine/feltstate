"""Tests for one-turn orchestration (companion.round)."""

from __future__ import annotations

from feltstate import Engine, KeywordSource
from feltstate.companion import EchoBackend, LLMBackend, companion_turn, extract_emotion_tag
from feltstate.companion.round import cap_history


def _eng(tmp_path) -> Engine:
    return Engine(source=KeywordSource(), state_path=str(tmp_path / "state.json"))


class CapturingBackend(LLMBackend):
    """Records the messages it was handed so a test can inspect what was sent."""

    def __init__(self) -> None:
        self.seen: list[dict] = []

    def complete(self, messages: list[dict]) -> str:
        self.seen = list(messages)
        return "ok"


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
    backend = CapturingBackend()
    companion_turn(eng, backend, [], "hello", system_prompt="THE-SYSTEM")
    assert backend.seen[0]["role"] == "system"
    assert backend.seen[0]["content"] == "THE-SYSTEM"
    assert backend.seen[-1]["role"] == "user"


# --- history cap (#48) + bounded send (#49) --------------------------------- #


def test_cap_history_trims_in_place_to_most_recent():
    hist = [{"role": "user", "content": str(i)} for i in range(10)]
    cap_history(hist, 4)
    assert len(hist) == 4
    assert [m["content"] for m in hist] == ["6", "7", "8", "9"]  # newest kept


def test_cap_history_none_or_nonpositive_is_noop():
    hist = [{"role": "user", "content": str(i)} for i in range(5)]
    cap_history(hist, None)
    assert len(hist) == 5
    cap_history(hist, 0)
    assert len(hist) == 5


def test_history_stays_within_cap_across_turns(tmp_path):
    eng = _eng(tmp_path)
    history: list[dict] = []
    for i in range(20):
        companion_turn(eng, EchoBackend(), history, f"msg {i}", system_prompt="SYS", history_cap=6)
    # Each turn adds a user+assistant pair; the rolling cap holds the total down.
    assert len(history) == 6
    assert history[-1]["role"] == "assistant"


def test_only_capped_window_is_sent_to_backend(tmp_path):
    eng = _eng(tmp_path)
    backend = CapturingBackend()
    history: list[dict] = []
    for i in range(20):
        companion_turn(eng, backend, history, f"msg {i}", system_prompt="SYS", history_cap=6)
    # The last call saw: system + at most `history_cap` prior turns + the fresh
    # injected user turn — never the whole 40-message conversation.
    assert backend.seen[0]["role"] == "system"
    prior = backend.seen[1:-1]
    assert len(prior) <= 6
    assert backend.seen[-1]["role"] == "user"


# --- proactive marker (#51): internal prompt is not a user turn ------------- #


def test_proactive_record_role_not_stored_as_user(tmp_path):
    eng = _eng(tmp_path)
    history: list[dict] = []
    companion_turn(
        eng,
        EchoBackend(),
        history,
        "internal: ask how the deploy went",
        system_prompt="SYS",
        skip_tick=True,
        record_role="proactive",
    )
    # The proactive prompt is recorded under a non-user marker, not role=user...
    prompt_turns = [m for m in history if m["content"] == "internal: ask how the deploy went"]
    assert prompt_turns and prompt_turns[0]["role"] == "proactive"
    assert all(m["role"] != "user" for m in history)  # no user-role turn at all
    # ...and the assistant reply is still recorded as normal context.
    assert history[-1]["role"] == "assistant"


def test_proactive_prompt_not_replayed_as_user_turn(tmp_path):
    eng = _eng(tmp_path)
    backend = CapturingBackend()
    history: list[dict] = []
    # A prior proactive turn leaves a "proactive"-role marker + an assistant reply.
    companion_turn(
        eng,
        EchoBackend(),
        history,
        "proactive nudge",
        system_prompt="SYS",
        skip_tick=True,
        record_role="proactive",
    )
    # Now a real user turn: the backend must NOT see the earlier proactive prompt
    # replayed as a user message (that would make the model think the user said it).
    companion_turn(eng, backend, history, "hi there", system_prompt="SYS")
    replayed_user = [m for m in backend.seen if m.get("role") == "user"]
    assert all("proactive nudge" not in m.get("content", "") for m in replayed_user)
    # The assistant reply from the proactive turn is still replayed (real context).
    assert any(m.get("role") == "assistant" for m in backend.seen)
