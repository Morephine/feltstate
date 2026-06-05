"""Tests for the Companion facade: a foreground turn + a proactive tick, all fakes."""

from __future__ import annotations

import asyncio
import random
from datetime import datetime
from typing import Any

from feltstate import KeywordSource
from feltstate.companion import (
    AlwaysIdlePresence,
    Companion,
    CompanionConfig,
    EchoBackend,
    NullFrontend,
    NullVoice,
    RandomSource,
    SchedulerConfig,
)


class RecFrontend(NullFrontend):
    def __init__(self) -> None:
        self.tokens: list[Any] = []

    def label_to_token(self, label: str) -> Any | None:
        return label  # identity map: any label becomes a token

    async def push_expression(self, token: Any) -> bool:
        self.tokens.append(token)
        return True


class RecVoice(NullVoice):
    def __init__(self) -> None:
        self.spoken: list[tuple[str, str]] = []

    async def synthesize(self, text: str, emotion_hint: str = "") -> str | None:
        self.spoken.append((text, emotion_hint))
        return None


def _cfg(tmp_path, **kw) -> CompanionConfig:
    return CompanionConfig(
        persona="a steady companion",
        system_prompt="SYS",
        state_path=str(tmp_path / "state.json"),
        scheduler_state_path=str(tmp_path / "sch.json"),
        **kw,
    )


def test_say_runs_and_voices(tmp_path):
    voice = RecVoice()
    pet = Companion(
        _cfg(tmp_path),
        source=KeywordSource(),
        backend=EchoBackend(),
        frontend=RecFrontend(),
        voice=voice,
        presence=AlwaysIdlePresence(),
    )
    result = asyncio.run(pet.say("I'm so happy and grateful, thank you!!"))
    assert result.reply
    assert voice.spoken  # the reply has speakable text -> synthesize called
    assert pet.history and pet.history[-1]["role"] == "assistant"


def test_null_adapters_text_only(tmp_path):
    pet = Companion(
        _cfg(tmp_path),
        source=KeywordSource(),
        backend=EchoBackend(),
        frontend=NullFrontend(),
        voice=NullVoice(),
        presence=AlwaysIdlePresence(),
    )
    result = asyncio.run(pet.say("hello"))
    assert result.reply  # works with no skin and no voice (e.g. Discord/text)


def test_proactive_tick_dispatches_and_voices(tmp_path):
    voice = RecVoice()
    fire = RandomSource(["I was just thinking about you"], probability=1.0, rng=random.Random(0))
    pet = Companion(
        _cfg(tmp_path, scheduler=SchedulerConfig(boot_grace_s=0)),
        source=KeywordSource(),
        backend=EchoBackend(),
        frontend=RecFrontend(),
        voice=voice,
        presence=AlwaysIdlePresence(),
        extra_sources=[fire],
    )
    fired = pet.scheduler.tick_once(now=datetime(2026, 6, 5, 10, 0, 0))
    assert fired == "random"
    # the proactive line was routed through the companion's own speak path
    assert voice.spoken
