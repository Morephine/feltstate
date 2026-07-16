"""Tests for the Companion facade: a foreground turn + a proactive tick, all fakes."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import threading
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


def test_proactive_prompt_never_enters_history_as_user(tmp_path):
    # #51: a heartbeat-initiated proactive prompt must not be recorded as a
    # role=user turn — otherwise the model reads its own prompt as the user's
    # next message. It may be kept under a non-user marker; the reply stays.
    fire = RandomSource(["ask how the deploy went"], probability=1.0, rng=random.Random(0))
    pet = Companion(
        _cfg(tmp_path, scheduler=SchedulerConfig(boot_grace_s=0)),
        source=KeywordSource(),
        backend=EchoBackend(),
        frontend=NullFrontend(),
        voice=NullVoice(),
        presence=AlwaysIdlePresence(),
        extra_sources=[fire],
    )
    assert pet.scheduler.tick_once(now=datetime(2026, 6, 5, 10, 0, 0)) == "random"
    # The proactive prompt text is present but NOT under role=user.
    user_turns = [m for m in pet.history if m.get("role") == "user"]
    assert all("ask how the deploy went" not in m.get("content", "") for m in user_turns)
    assert not any(m.get("role") == "user" for m in pet.history)
    # The companion's own spoken reply is still in history as normal context.
    assert pet.history and pet.history[-1]["role"] == "assistant"


def test_say_and_scheduler_tick_do_not_race_on_engine_save(tmp_path):
    # Regression for the reproduced FileNotFoundError: foreground say() and the
    # scheduler's idle eng.tick([]) both drive Engine.save() through the same
    # state.json.tmp. Before the shared lock they could replace() the same temp
    # file concurrently and crash. They must serialise on the Companion lock, and
    # this hits the REAL scheduler path (tick_once), not just the dispatcher.
    pet = Companion(
        _cfg(tmp_path, scheduler=SchedulerConfig(boot_grace_s=0)),
        source=KeywordSource(),
        backend=EchoBackend(),
        frontend=NullFrontend(),
        voice=NullVoice(),
        presence=AlwaysIdlePresence(),
    )
    errors: list[Exception] = []

    def hammer_say() -> None:
        try:
            for _ in range(60):
                asyncio.run(pet.say("hello there"))
        except Exception as e:
            errors.append(e)

    def hammer_tick() -> None:
        try:
            for _ in range(60):
                pet.scheduler.tick_once(now=datetime.now())
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=hammer_say), threading.Thread(target=hammer_tick)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent say()/tick_once() raced: {errors!r}"
    # state.json must still be valid JSON (not half-written / truncated).
    json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))


def test_history_respects_configured_cap(tmp_path):
    # #48: the private chat history is bounded by cfg.history_cap.
    pet = Companion(
        _cfg(tmp_path, history_cap=4),
        source=KeywordSource(),
        backend=EchoBackend(),
        frontend=NullFrontend(),
        voice=NullVoice(),
        presence=AlwaysIdlePresence(),
    )
    for i in range(10):
        asyncio.run(pet.say(f"turn {i}"))
    assert len(pet.history) == 4  # user+assistant pairs, capped
    assert pet.history[-1]["role"] == "assistant"


# --- P0-4: RLock serialises say() and heartbeat dispatcher ------------------- #


def test_companion_has_rlock():
    """Companion exposes _lock as a threading.RLock (not a plain Lock)."""

    pet = Companion(
        _cfg(__import__("pathlib").Path("/tmp")),
        source=KeywordSource(),
        backend=EchoBackend(),
        frontend=NullFrontend(),
        voice=NullVoice(),
        presence=AlwaysIdlePresence(),
    )
    # RLock can be acquired twice from the same thread without deadlock.
    assert pet._lock.acquire()
    assert pet._lock.acquire()  # re-entrant: would deadlock with a plain Lock
    pet._lock.release()
    pet._lock.release()


def test_say_holds_lock_during_turn(tmp_path):
    """The lock is held for the full duration of say() so a concurrent thread
    cannot interleave its own engine/history mutations.

    A *separate* thread tries a non-blocking acquire while say() is mid-turn;
    that attempt must fail because the main thread already holds the lock.
    RLock is re-entrant only for the *owning* thread, so a different thread
    sees it as locked.
    """
    lock_held_during_say: list[bool] = []
    probe_ready = threading.Event()
    probe_done = threading.Event()

    class SpyBackend(EchoBackend):
        def __init__(self, pet_ref):
            self._pet = pet_ref

        def complete(self, messages):
            # Signal the probe thread to attempt a non-blocking acquire, then
            # wait for it to finish before returning so the lock is still held.
            probe_ready.set()
            probe_done.wait(timeout=5)
            return "ok"

    pet = Companion(
        _cfg(tmp_path),
        source=KeywordSource(),
        backend=SpyBackend(None),
        frontend=NullFrontend(),
        voice=NullVoice(),
        presence=AlwaysIdlePresence(),
    )
    pet.backend = SpyBackend(pet)

    def probe():
        probe_ready.wait(timeout=5)
        # A different thread trying to acquire while say() holds it must fail.
        acquired = pet._lock.acquire(blocking=False)
        lock_held_during_say.append(not acquired)  # True = lock was held by other thread
        if acquired:
            pet._lock.release()
        probe_done.set()

    t = threading.Thread(target=probe)
    t.start()
    asyncio.run(pet.say("hello"))
    t.join(timeout=5)

    assert lock_held_during_say == [True], "lock must block a different thread while say() runs"


def test_concurrent_say_and_proactive_both_complete(tmp_path):
    """A foreground say() and a heartbeat _proactive_say() running from separate
    threads must both complete without error; the lock serialises them so history
    is never in a torn state."""
    errors: list[str] = []
    results: list[bool] = []

    pet = Companion(
        _cfg(tmp_path, scheduler=SchedulerConfig(boot_grace_s=0)),
        source=KeywordSource(),
        backend=EchoBackend(),
        frontend=NullFrontend(),
        voice=NullVoice(),
        presence=AlwaysIdlePresence(),
        extra_sources=[RandomSource(["heartbeat"], probability=1.0, rng=random.Random(0))],
    )

    def run_say():
        try:
            asyncio.run(pet.say("foreground"))
            results.append(True)
        except Exception as exc:
            errors.append(f"say: {exc}")

    def run_proactive():
        try:
            asyncio.run(pet._proactive_say("random", "heartbeat"))
            results.append(True)
        except Exception as exc:
            errors.append(f"proactive: {exc}")

    t1 = threading.Thread(target=run_say)
    t2 = threading.Thread(target=run_proactive)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not errors, f"threads raised: {errors}"
    assert len(results) == 2, "both turns must complete"
    # History must be consistent: both assistant replies are present, no torn state.
    assistant_turns = [m for m in pet.history if m.get("role") == "assistant"]
    assert len(assistant_turns) == 2


def test_concurrent_async_say_calls_are_serialized(tmp_path):
    """Two sibling coroutines on one event loop must not overlap adapter work.

    ``threading.RLock`` alone is re-entrant for the whole event-loop thread, so
    this regression test specifically exercises the coroutine-level lock.
    """

    class OverlapVoice(NullVoice):
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0

        async def synthesize(self, text: str, emotion_hint: str = "") -> str | None:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                await asyncio.sleep(0.02)
            finally:
                self.active -= 1
            return None

    voice = OverlapVoice()
    pet = Companion(
        _cfg(tmp_path),
        source=KeywordSource(),
        backend=EchoBackend(),
        frontend=NullFrontend(),
        voice=voice,
        presence=AlwaysIdlePresence(),
    )

    async def run_both() -> None:
        await asyncio.gather(pet.say("first"), pet.say("second"))

    asyncio.run(run_both())

    assert voice.max_active == 1
    assert [m["role"] for m in pet.history] == ["user", "assistant", "user", "assistant"]


# --- P1-4: dispatcher swallowed exceptions must be logged -------------------- #


def test_dispatcher_exception_is_logged_not_swallowed(tmp_path, caplog):
    """_CompanionDispatcher.dispatch must log the exception via logging.exception
    before returning False — so errors from _proactive_say are diagnosable and
    the scheduler heartbeat is not killed."""

    class BoomVoice(NullVoice):
        async def synthesize(self, text: str, emotion_hint: str = "") -> str | None:
            raise RuntimeError("TTS pipeline offline")

    # Use a highest-priority source so it is always attempted first.
    fire = RandomSource(["wake up"], probability=1.0, rng=random.Random(0), priority=0)
    pet = Companion(
        _cfg(tmp_path, scheduler=SchedulerConfig(boot_grace_s=0)),
        source=KeywordSource(),
        backend=EchoBackend(),
        frontend=NullFrontend(),
        voice=BoomVoice(),
        presence=AlwaysIdlePresence(),
        extra_sources=[fire],
    )

    with caplog.at_level(logging.ERROR, logger="feltstate.companion.app"):
        # tick_once must NOT raise — the dispatcher must catch and log the error.
        pet.scheduler.tick_once(now=datetime(2026, 6, 5, 10, 0, 0))

    # The exception must have been logged, not silently swallowed.
    assert any(
        ("proactive" in r.message.lower() or "dispatcher" in r.message.lower())
        and r.levelno >= logging.ERROR
        for r in caplog.records
    ), "a dispatcher exception must be logged at ERROR level via logging.exception"
