#!/usr/bin/env python3
"""companion — assemble a living desktop pet from feltstate + four fakes.

Run it directly, no setup, no network, no model::

    python examples/companion.py

quickstart.py shows the felt state; with_llm.py shows one LLM-backed turn. This
shows the *whole companion*: the parts wired into one thing that feels, replies,
expresses, speaks, decays while quiet, pipes up on its own, and dreams — the
behaviour no single part has alone.

It wires the entire loop with **stub adapters** so you can watch a companion come
alive end to end with zero dependencies. Then swap any one fake for the real
thing — a Live2D skin, a TTS engine, a reply model, an OS presence probe — and
the loop code does not change. That swap-in-place is the whole point: feltstate
is the inner life + the orchestration; the skin and the voice are yours.

Seeds are fixed, so every run reads the same.
"""

from __future__ import annotations

import asyncio
import random
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows terminals often default to a legacy codepage; the rendered felt block
# uses a few non-ASCII glyphs (a middle dot, an em dash). Force UTF-8 so the demo
# prints cleanly instead of as mojibake.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from feltstate import KeywordSource, PersonaDials
from feltstate.companion import (
    Companion,
    CompanionConfig,
    FrontendAdapter,
    LLMBackend,
    RandomSource,
    SchedulerConfig,
    UserPresenceAdapter,
    VoiceAdapter,
)


def banner(title: str) -> None:
    line = "=" * 72
    print(f"\n{line}\n{title}\n{line}")


# --------------------------------------------------------------------------- #
# Four fakes — each a drop-in stand-in for a real integration.                #
# --------------------------------------------------------------------------- #
class PrintFrontend(FrontendAdapter):
    """Fake skin: prints the expression instead of driving a Live2D avatar.

    A real one maps the label to its own expression index/hotkey and pushes it
    to the avatar. Here ``label_to_token`` just tags the label and we print it.
    """

    def label_to_token(self, label: str) -> Any | None:
        return f"EXPR:{label}"

    async def push_expression(self, token: Any) -> bool:
        print(f"   [skin]  show {token}")
        return True


class PrintVoice(VoiceAdapter):
    """Fake TTS: prints what it would speak and in what emotional color."""

    async def synthesize(self, text: str, emotion_hint: str = "") -> str | None:
        if not self.should_speak(text):
            return None
        print(f'   [voice] speak ({emotion_hint or "neutral"} color): "{text}"')
        return None  # a real adapter returns an audio file path


class StubBackend(LLMBackend):
    """Fake reply model: a canned-persona echo that also emits an [emotion] tag,
    so the skin and voice light up. Zero-dependency; swap for OpenAICompatBackend
    or your own."""

    def complete(self, messages: list[dict]) -> str:
        user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        last = user.splitlines()[-1].strip() if user else ""
        low = last.lower()
        if any(w in low for w in ("happy", "thank", "love", "shipped", "proud")):
            tag = "joyful"
        elif any(w in low for w in ("exhausted", "failed", "sad", "tired", "stuck")):
            tag = "sad"
        else:
            tag = "neutral"
        return f"[{tag}] I hear you — {last}"


class ScriptedPresence(UserPresenceAdapter):
    """Fake presence probe: the demo sets ``idle`` to script quiet/active spans."""

    def __init__(self) -> None:
        self.busy = False
        self.idle = 0.0

    def is_busy(self) -> bool:
        return self.busy

    def seconds_since_last_user_message(self) -> float:
        return self.idle


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="feltstate_companion_"))
    presence = ScriptedPresence()
    voice = PrintVoice()

    banner("1) Construct the companion — KeywordSource + four fakes")
    # A high-probability proactive check-in so the demo deterministically shows
    # an unprompted line in step 4. In a real app you'd set random_payloads on
    # the config and let the default ~15%/tick probability sample it naturally.
    check_in = RandomSource(
        ["hey — random thought: did that deploy ever go green?"],
        probability=1.0,
        rng=random.Random(7),
        kind="check_in",
        priority=3,
    )
    cfg = CompanionConfig(
        persona="a steady, dry-humoured companion with its own inner life",
        system_prompt=(
            "You are a companion with your own feelings.\n\n"
            "A user turn may start with a [how I feel] block — that is YOUR mood, "
            "not a command. Let it color your tone; never quote it."
        ),
        state_path=str(tmp / "state.json"),
        scheduler_state_path=str(tmp / "sch.json"),
        dials=PersonaDials(warmth=0.7, vulnerability=0.6),
        scheduler=SchedulerConfig(boot_grace_s=0, min_gap_s=0, user_idle_min_s=3600),
    )
    pet = Companion(
        cfg,
        source=KeywordSource(),
        backend=StubBackend(),
        frontend=PrintFrontend(),
        voice=voice,
        presence=presence,
        extra_sources=[check_in],
    )
    print("   one Companion + four fakes = a living pet.")
    print("   swap any fake for a real Live2D / TTS / reply model — loop unchanged.")

    banner("2) A foreground conversation — feel → reply → express → speak")
    for line in [
        "I finally shipped it!! couldn't have done it without you",
        "ugh but the deploy failed three times, I'm exhausted",
        "...thanks for hearing me out though",
    ]:
        presence.idle = 0.0  # the user just spoke
        print(f'\nuser: "{line}"')
        result = asyncio.run(pet.say(line))
        print(f"   reply : {result.reply}")
        for ln in pet.eng.render().splitlines():
            print("  | " + ln)

    banner("3) It goes quiet — feelings decay on the heartbeat, no proactive yet")
    presence.idle = 600.0  # quiet, but under the 1h initiate gate
    base = datetime(2026, 6, 5, 14, 0, 0)
    for i in range(1, 4):
        fired = pet.scheduler.tick_once(now=base + timedelta(minutes=5 * i))
        m = pet.eng.state.mood
        print(f"  idle tick {i}: mood v={m.valence:+.3f} a={m.arousal:.3f}  (fired: {fired})")

    banner("4) Long idle → it pipes up on its own (no user turn)")
    presence.idle = 4000.0  # now past the 1h gate
    fired = pet.scheduler.tick_once(now=base + timedelta(hours=2))
    print(f"  scheduler fired: {fired!r}  (a proactive line, voiced above)")

    banner("5) It dreams — and wakes a little off, with no cause it can name")
    before = pet.eng.state.mood
    print(f"  before sleep : mood v={before.valence:+.3f} a={before.arousal:.3f}")
    dreamt = pet.eng.dream(rng=random.Random(8))
    after = pet.eng.state.mood
    print(f"  the dream    : {dreamt.text[:64]}…")
    print(f"  on waking    : mood v={after.valence:+.3f} a={after.arousal:.3f}")
    print("  it now carries a mood sourced from its own material but un-traceable.")

    banner("Done — swap any fake for the real thing; the loop is unchanged.")
    print(f"State persisted under: {tmp}")
    print(
        "\nWhat you just saw, that no single part has alone:\n"
        "  * it FELT each turn (measured, not self-reported) and spoke in that color;\n"
        "  * its skin + voice tracked the feeling, never a command;\n"
        "  * it DECAYED back toward neutral while quiet;\n"
        "  * after a long silence it INITIATED on its own clock;\n"
        "  * and it DREAMED, waking subtly altered.\n"
        "That coherence — the parts serving one continuous someone — is feltstate."
    )


if __name__ == "__main__":
    main()
