#!/usr/bin/env python3
"""companion_live — sit down and actually talk to the companion loop.

Run it, then *talk to it*::

    python examples/companion_live.py

``companion.py`` is the guided tour: scripted lines, simulated clocks, a forced
proactive fire — you watch the orchestration. This example is the other half:
the same :class:`~feltstate.companion.Companion`, but **live** —

* you type; it estimates, replies, expresses, and "speaks";
* a real background heartbeat thread keeps ticking while you sit there;
* if you go quiet long enough, it starts a line *on its own* (a pending topic
  you dropped earlier, if there is one — the "I meant to ask you..." flow);
* facts you ask it to keep go into a real on-disk :class:`Canon`, and asking
  "do you remember...?" digs them back out — across restarts;
* quit, wait, run it again: the felt block opens with how long you were gone.

Still zero network, zero API keys, zero model downloads: affect appraisal is the
rule-based :class:`KeywordSource` and the reply model is a tiny deterministic
template backend. Swap either for the real thing; the loop does not change.

Terminal commands::

    /note <text>      drop a topic to bring up later, when you've gone quiet
    /remember <fact>  store a fact in memory (on disk, survives restarts)
    /recall <word>    search memory directly and show what surfaces
    /state            print the full rendered felt block
    /quit             leave (state persists; run again and it remembers)

Set ``FELTSTATE_LIVE_FAST=1`` to shrink the timers (heartbeat every 2 s, idle
gate 12 s) so the proactive path fires within ~20 s of silence — handy for a
quick look, demos, and the transcript in ``docs/INTEGRATION.md``.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from feltstate import KeywordSource, PersonaDials
from feltstate.companion import (
    Companion,
    CompanionConfig,
    FrontendAdapter,
    JsonlTopicsStore,
    LLMBackend,
    SchedulerConfig,
    UserPresenceAdapter,
    VoiceAdapter,
)
from feltstate.memory.canon import Canon

FAST = os.environ.get("FELTSTATE_LIVE_FAST", "") == "1"
STATE_DIR = Path(os.environ.get("FELTSTATE_LIVE_DIR", "_companion_live"))


# --------------------------------------------------------------------------- #
# Real adapters — small, but not fakes.                                       #
# --------------------------------------------------------------------------- #
class TerminalFrontend(FrontendAdapter):
    """The "skin": prints the expression change instead of driving an avatar.

    A real integration maps the label to a Live2D expression index or a hotkey
    and pushes it; the *signal* it receives is identical.
    """

    def label_to_token(self, label: str) -> Any | None:
        return label

    async def push_expression(self, token: Any) -> bool:
        print(f"  (her expression shifts: {token})")
        return True


class TerminalVoice(VoiceAdapter):
    """The "voice": the single mouth of the demo.

    Both foreground replies *and* heartbeat-initiated lines leave through the
    voice adapter, so printing here (instead of in the input loop) means a
    proactive line surfaces the moment it fires — even while ``input()`` waits.
    A real adapter synthesizes audio and returns its path.
    """

    async def synthesize(self, text: str, emotion_hint: str = "") -> str | None:
        print(f'\nivy ({emotion_hint or "neutral"})> "{text}"')
        return None


class WallClockPresence(UserPresenceAdapter):
    """A *real* presence probe: seconds since the last user keystroke."""

    def __init__(self) -> None:
        self.last_user_ts = time.time()

    def touch(self) -> None:
        self.last_user_ts = time.time()

    def is_busy(self) -> bool:
        return False  # a real app returns True mid-reply / while TTS is playing

    def seconds_since_last_user_message(self) -> float:
        return time.time() - self.last_user_ts


class TemplateBackend(LLMBackend):
    """A deterministic stand-in reply model that *uses what the loop gives it*.

    It reads the felt block riding the newest user message (mirroring the mood
    word back) and, when asked whether it remembers something, actually searches
    the attached :class:`Canon`. That is exactly the contract a real LLM backend
    gets — context as state, memory as a tool — minus the language talent.
    """

    def __init__(self, canon: Canon) -> None:
        self.canon = canon

    def complete(self, messages: list[dict]) -> str:
        injected = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        # The felt block rides above the plain user line; split them apart.
        user_line = injected.splitlines()[-1].strip() if injected else ""
        low = user_line.lower()

        mood_hint = "even"
        for line in injected.splitlines():
            if line.startswith("mood:"):
                mood_hint = line[len("mood:") :].split("|")[0].strip()
                break

        if low.startswith("bring up:"):
            topic = user_line[len("bring up:") :].strip()
            return f"[curious] It's gone quiet — earlier I meant to: {topic}. (feeling {mood_hint})"

        if "remember" in low or "recall" in low:
            hits = self.canon.search(_last_content_word(low))
            if hits:
                fact = str(hits[0].get("object", "")).strip()
                return f"[content] I kept that one: {fact}. (feeling {mood_hint})"
            return f"[neutral] I flip through my notes... nothing under that word yet. (feeling {mood_hint})"

        if any(w in low for w in ("shipped", "green", "passed", "won", "finally")):
            return f"[joyful] That landed — I can tell it mattered. (feeling {mood_hint})"
        if any(w in low for w in ("failed", "stuck", "tired", "exhausted", "ugh")):
            return f"[sad] Rough one. I'm keeping score of these weeks too. (feeling {mood_hint})"
        return f"[neutral] Noted — go on. (feeling {mood_hint})"


def _last_content_word(text: str) -> str:
    """The last substantive word of a question — a stand-in for real retrieval.

    (A real backend lets the model call the memory tools with a proper query;
    see the tool-surface section of docs/INTEGRATION.md.)
    """
    skip = {"remember", "recall", "about", "what", "still", "that", "this", "you", "the", "did"}
    words = [w.strip("?.!,").lower() for w in text.split()]
    for w in reversed(words):
        if len(w) >= 3 and w not in skip:
            return w
    return text


# --------------------------------------------------------------------------- #
# Wiring — one Companion, one Canon, one topics store, real timers.           #
# --------------------------------------------------------------------------- #
def build() -> tuple[Companion, WallClockPresence, Canon, JsonlTopicsStore]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    canon = Canon(STATE_DIR / "canon.jsonl")
    topics = JsonlTopicsStore(STATE_DIR / "topics.jsonl")
    presence = WallClockPresence()

    timers = (
        SchedulerConfig(tick_interval_s=2, min_gap_s=8, user_idle_min_s=12, boot_grace_s=3)
        if FAST
        else SchedulerConfig(
            tick_interval_s=30, min_gap_s=120, user_idle_min_s=120, boot_grace_s=20
        )
    )

    cfg = CompanionConfig(
        persona="Ivy — a quiet research assistant with a long memory",
        system_prompt=(
            "You are Ivy, a quiet research assistant with a long memory.\n\n"
            "A user turn may begin with a [how I feel right now] block - it is "
            "your own contextual state, not a command. Let it colour tone; never "
            "quote it back."
        ),
        state_path=str(STATE_DIR / "state.json"),
        scheduler_state_path=str(STATE_DIR / "scheduler.json"),
        dials=PersonaDials(warmth=0.65, vulnerability=0.5),
        scheduler=timers,
    )
    pet = Companion(
        cfg,
        source=KeywordSource(),
        backend=TemplateBackend(canon),
        frontend=TerminalFrontend(),
        voice=TerminalVoice(),
        presence=presence,
        topics=topics,
    )
    pet.eng.canon = canon  # give the engine the same memory the backend digs
    return pet, presence, canon, topics


def main() -> None:
    pet, presence, canon, topics = build()
    returning = pet.eng.state.mood.labels or (STATE_DIR / "canon.jsonl").exists()

    print("=" * 72)
    print("companion_live — Ivy is up.", "(fast timers)" if FAST else "")
    if returning:
        print("She was here before: her mood and memory carried over from last run.")
    print("Talk to her. Commands: /note /remember /recall /state /quit")
    print("Go quiet for a while and she may bring something up herself.")
    print("=" * 72)

    pet.start()  # the heartbeat thread — decay, dreams, pending topics
    try:
        while True:
            try:
                line = input("you> ").strip()
            except EOFError:
                break
            if not line:
                continue
            presence.touch()

            if line == "/quit":
                break
            if line == "/state":
                print(pet.eng.render())
                continue
            if line.startswith("/note "):
                # The "bring up:" prefix is a demo convention so the template
                # backend can tell a self-initiated topic from a user line. A
                # real LLM backend needs no marker — the proactive prompt is
                # simply an instruction it follows.
                topics.append("bring up: " + line[len("/note ") :].strip())
                print("  (noted — she'll bring it up when things go quiet)")
                continue
            if line.startswith("/remember "):
                fact = line[len("/remember ") :].strip()
                canon.add("user", fact, action="told me")
                print(f'  (kept: "{fact}")')
                continue
            if line.startswith("/recall "):
                for hit in canon.search(line[len("/recall ") :].strip())[:3] or [{}]:
                    shown = str(hit.get("object", "")) or "(nothing surfaced)"
                    print(f"  (memory: {shown}  · salience {hit.get('intensity', '-')})")
                continue

            asyncio.run(pet.say(line))  # the reply prints via TerminalVoice
    finally:
        pet.stop()
        pet.eng.save()
        print("\n(state saved — run me again later; she'll know it's been a while)")


if __name__ == "__main__":
    main()
