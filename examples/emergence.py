#!/usr/bin/env python3
"""emergence — memory that finds its own way back: RES / EMG / SPK.

Run it directly, no setup, no network, no dependencies beyond the standard
library and feltstate itself::

    python examples/emergence.py

Recall the agent *chooses* (``search`` / ``recall`` / ``reach``) is half the
story. A companion also has memory that surfaces **uninvited** — the note it
keeps in its pocket, the thought that pops up mid-conversation, the flashback
that hits when feeling spikes. This example is the reference pattern for that
consumer, three doors over one store:

* **RES — resident notes.** The top few most-salient live facts, re-picked
  every turn. No dice: these are simply what the agent is carrying around.
* **EMG — emergence.** A dice roll (defaults to 12% per turn) decides whether
  *anything* pops up; if it fires, one fact is softmax-sampled by
  mood-congruence × salience, and goes on **cooldown** so it cannot pester.
  Low-probability tails stay alive — cold memories get their day.
* **SPK — spike.** When arousal *jumps*, the most emotionally charged,
  mood-congruent memory fires as a flashback — no dice, past a threshold it
  simply happens, carrying its recorded feeling with it.

The pattern is deliberately mechanical: dice, cooldowns, one softmax — no
model call decides what surfaces. What was *felt* at write time (the recorded
valence and charge) does the steering. Deterministic: seeded RNG, scripted
mood arc.
"""

from __future__ import annotations

import math
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from feltstate.memory.canon import Canon  # noqa: E402

DICE_P = 0.12  # EMG: chance per turn that anything pops up at all
COOLDOWN_TURNS = 4  # EMG: a surfaced fact stays quiet this many turns
SPIKE_JUMP = 0.35  # SPK: arousal rise (vs previous turn) that forces a flashback
SOFTMAX_T = 0.6  # EMG: sampling temperature (higher = colder tails live more)


def congruence(fact_valence: float, mood_valence: float) -> float:
    """1 when the fact leans with the mood, 0 when dead against it."""
    return 1.0 - abs(fact_valence - mood_valence) / 2.0


def pick_softmax(rng: random.Random, weighted: list[tuple[float, dict]]) -> dict:
    zs = [w / SOFTMAX_T for w, _ in weighted]
    m = max(zs)
    exps = [math.exp(z - m) for z in zs]
    total = sum(exps)
    roll = rng.random() * total
    acc = 0.0
    for e, (_, f) in zip(exps, weighted, strict=True):
        acc += e
        if roll <= acc:
            return f
    return weighted[-1][1]


def main() -> None:
    rng = random.Random(3)
    with tempfile.TemporaryDirectory() as td:
        canon = Canon(Path(td) / "canon.jsonl")

        seed = [
            ("ash", "the rent freeze became official", 0.85, +0.8),
            ("ash", "knocked the kettle off the counter", 0.35, -0.3),
            ("Kay", "helped fix the garden fence", 0.7, +0.6),
            ("ash", "the burst pipe ruined the good rug", 0.8, -0.8),
            ("ash", "planted tomatoes by the fence", 0.5, +0.3),
            ("ash", "a passing thought about socks", 0.2, 0.0),
        ]
        for actor, obj, inten, val in seed:
            canon.add(actor, obj, intensity=inten, emotion=val)

        # A scripted afternoon: calm -> warming -> a sudden fright -> settling.
        moods = [
            (+0.2, 0.30),
            (+0.3, 0.35),
            (+0.4, 0.40),
            (+0.5, 0.45),
            (+0.4, 0.40),
            (-0.5, 0.85),  # <- the spike: bad news lands
            (-0.3, 0.60),
            (-0.1, 0.45),
            (+0.1, 0.40),
            (+0.2, 0.35),
        ]

        cooldown: dict[str, int] = {}
        prev_arousal = moods[0][1]

        print("turn  mood(v,a)   what surfaced")
        print("----  ----------  " + "-" * 58)
        for t, (mv, ma) in enumerate(moods, 1):
            rows = canon.view()
            lines: list[str] = []

            # RES — the pocket note: the single most salient live fact.
            res = max(rows, key=lambda r: r["intensity"])
            lines.append(f"RES  {res['object'][:44]}")

            # SPK — arousal jumped: the most charged, mood-leaning memory fires.
            if ma - prev_arousal >= SPIKE_JUMP:
                spk = max(rows, key=lambda r: r["charge"] * congruence(r["valence"], mv))
                lines.append(f"SPK  !! {spk['object'][:41]} (felt {spk['valence']:+.1f})")
            # EMG — otherwise the dice decide if anything pops up at all.
            elif rng.random() < DICE_P:
                pool = [
                    (congruence(r["valence"], mv) * (0.4 + r["intensity"]), r)
                    for r in rows
                    if cooldown.get(r["id"], 0) < t
                ]
                if pool:
                    got = pick_softmax(rng, pool)
                    cooldown[got["id"]] = t + COOLDOWN_TURNS
                    lines.append(f"EMG  ~ {got['object'][:44]}")

            prev_arousal = ma
            first, *rest = lines
            print(f"{t:>4}  ({mv:+.1f},{ma:.2f})  {first}")
            for x in rest:
                print(" " * 18 + x)

        print("\nRES rotates with salience; EMG is rare and cooled-down on purpose;")
        print("SPK fired exactly once — when feeling jumped, not when a model chose.")


if __name__ == "__main__":
    main()
