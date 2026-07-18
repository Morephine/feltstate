#!/usr/bin/env python3
"""agent_narration — keeping the chat window alive while the agent works.

Run it::

    python examples/agent_narration.py

When the reply backend is an *agent* — tool calls, minutes of work — the
companion's chat window faces two UX cliffs:

1. **Silence reads as death.** Ten quiet minutes of hard work and a crashed
   process look identical from the outside.
2. **Raw telemetry reads as a machine.** Streaming tool logs at the user is
   worse than silence — the companion stops being a character.

The pattern demonstrated here (used by both ``docs/AGENT_WORK_UX.md`` and
``docs/FAILURE_IN_CHARACTER.md``): a **canned voicebank** — small pools of
in-character lines keyed by *event type*, sampled at random, throttled, with
progress checkpoints and completion cues; plus **in-character failure lines**
chosen by a quick diagnosis instead of leaking the exception.

Everything below is deterministic (seeded RNG, simulated clock): the printed
session is the exact transcript quoted in the docs. The pattern is app-side
by design — the library's contract ends at the reply; how you narrate the
*making* of the reply is your loop's voice.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# --------------------------------------------------------------------------- #
# 1. The voicebank — pools per event type, every line tagged.                 #
# --------------------------------------------------------------------------- #
VOICEBANK: dict[str, list[str]] = {
    # starting a stretch of work — one short acknowledgement, then hands busy
    "start": [
        "[focused] On it — give me a moment.",
        "[focused] Let me dig into that.",
        "[neutral] Taking a look now.",
    ],
    # per-tool narration pools: same act, several phrasings, so ten tool calls
    # don't produce ten identical lines. Match the *kind* of act, not the tool
    # name — reading and searching feel different to watch.
    "tool.search": [
        "[curious] Searching around for that...",
        "[curious] Let me see what's out there.",
    ],
    "tool.read": [
        "[focused] Reading through it.",
        "[neutral] Skimming this part.",
    ],
    # memory lookups get a *thinking* pool, not a *typing* pool — digging
    # through one's own notes is remembering, not operating a terminal.
    "tool.memory": [
        "[neutral] Hold on, that rings a bell...",
        "[neutral] Let me think back.",
    ],
    # tool outcomes: three pools, not one — success, failure, empty-handed
    "result.ok": ["[content] Got it.", "[smile] Found it."],
    "result.fail": [
        "[worried] Hm, that path didn't work — trying another way.",
        "[worried] That one bounced. One more angle.",
    ],
    "result.empty": ["[neutral] Nothing there. Moving on."],
    # progress checkpoints — the "still alive" heartbeat, with a step count
    "progress": [
        "[focused] {n} steps in, still going.",
        "[neutral] Step {n} — on track, this'll take a bit.",
    ],
    # completion cues — distinct from any mid-work line
    "done.ok": ["[joy] Done — here's what I found."],
    "done.fail": ["[sad] I couldn't crack it this time. Here's how far I got."],
}

# in-character failure lines, keyed by a *diagnosis*, not an exception class
FAILURE_LINES = {
    "net_down": "[worried] I can't reach the outside world right now — the network's gone quiet. Try me again in a bit?",
    "model_stalled": "[worried] My train of thought stalled mid-sentence. Ask me that once more?",
    "interrupted": "[neutral] Okay — dropping that. What's next?",
}


class Narrator:
    """Samples the voicebank with a minimum gap between lines (throttle)."""

    def __init__(self, min_gap_s: float = 8.0, seed: int = 11) -> None:
        self.rng = random.Random(seed)
        self.min_gap_s = min_gap_s
        self._last_at = -1e9
        self.steps = 0

    def say(self, t: float, pool: str, *, force: bool = False, **fmt) -> None:
        """Emit one line from ``pool`` at simulated time ``t`` — unless a line
        was already spoken within ``min_gap_s`` (start/done/failure force through)."""
        if not force and (t - self._last_at) < self.min_gap_s:
            return  # throttled: the work continues, the mouth rests
        line = self.rng.choice(VOICEBANK[pool]).format(**fmt)
        self._last_at = t
        print(f"  [t+{t:>4.0f}s] {line}")

    def step(self, t: float, every: int = 5) -> None:
        """Count a unit of work; every ``every`` steps, offer a progress line."""
        self.steps += 1
        if self.steps % every == 0:
            self.say(t, "progress", n=self.steps)


# --------------------------------------------------------------------------- #
# 2. A simulated stretch of agent work (deterministic timeline).              #
# --------------------------------------------------------------------------- #
def working_session() -> None:
    print("=" * 72)
    print("A. ten minutes of agent work, narrated — throttled, in character")
    print("=" * 72)
    n = Narrator(min_gap_s=8.0, seed=11)
    n.say(0, "start", force=True)

    # a burst of quick tool calls — the throttle keeps it from becoming spam
    for t, pool in [(2, "tool.search"), (4, "tool.read"), (6, "tool.read")]:
        n.say(t, pool)
        n.step(t)
    n.say(12, "result.ok")
    n.step(12)

    # deeper in: a memory dig, a failure, recovery — spaced out
    n.say(25, "tool.memory")
    n.step(25)
    n.say(40, "result.fail")
    n.step(40)
    n.say(55, "tool.search")
    n.step(55)  # step 7
    for t in (70, 85, 100):
        n.step(t)  # quiet grind; step 10 fires a progress line at t=100
    n.say(120, "result.empty")
    n.say(150, "tool.read")
    n.step(150)
    n.step(170)
    n.say(180, "done.ok", force=True)


# --------------------------------------------------------------------------- #
# 3. Failure, in character — diagnose first, then speak as yourself.          #
# --------------------------------------------------------------------------- #
def flaky_backend(mode: str) -> str:
    if mode == "timeout":
        raise TimeoutError("upstream inference timed out after 300s")
    if mode == "net":
        raise ConnectionError("[Errno 101] Network is unreachable")
    return "[content] All done."


def diagnose(exc: Exception) -> str:
    """Map an exception to a *felt* failure kind. Log the real one elsewhere."""
    if isinstance(exc, ConnectionError):
        return "net_down"
    if isinstance(exc, TimeoutError):
        return "model_stalled"
    return "model_stalled"


def failure_session() -> None:
    print()
    print("=" * 72)
    print("B. when it breaks, it breaks as herself — never a stack trace")
    print("=" * 72)
    for mode in ("net", "timeout"):
        try:
            flaky_backend(mode)
        except Exception as exc:  # noqa: BLE001 - the demo point is the catch-all seam
            kind = diagnose(exc)
            print(f"  (logged for the operator: {type(exc).__name__}: {exc})")
            print(f"  spoken to the user:  {FAILURE_LINES[kind]}")
    print()
    print("The user hears a person having trouble; the operator's log keeps the")
    print("real exception. Neither audience gets the other one's version.")


if __name__ == "__main__":
    working_session()
    failure_session()
