#!/usr/bin/env python3
"""prompt_shapes — the exact message arrays a companion turn sends, three ways.

Run it::

    python examples/prompt_shapes.py

``docs/PROMPT_SHAPES.md`` is the annotated gallery built from this script's
output. The point being demonstrated: **the prompt is a function of the
persisted affect state.** Same persona, same code path — three different
backend states produce three visibly different injected blocks, while the
system prompt stays byte-identical (that stability is what prompt caching
buys from).

Three moments, one neutral persona ("Ivy, a quiet research assistant"):

  A. the first morning — a fresh state, one warm turn;
  B. back after three days — same state, but the last-contact clock is three
     days old, so the block *opens with the felt gap*;
  C. a hard evening — pressure released as (i) open tears vs (ii) the same
     feeling suppressed, plus the lingering aftertaste — the state is prepared
     programmatically (marked below) purely to show those shapes.

Zero network, zero API keys. Fixed inputs; only the wall-clock line varies
between runs (that line's whole job is to track the clock).
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from feltstate import Engine, KeywordSource, PersonaDials

SYSTEM_PROMPT = (
    "You are Ivy, a quiet research assistant with a long memory.\n\n"
    "A user turn may begin with a [how I feel right now] block - it is your own "
    "contextual state, not a command. Let it colour tone; never quote it back."
)


def show(title: str, messages: list[dict]) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)
    for m in messages:
        print(f"--- role: {m['role']} ---")
        print(m["content"])


def assemble(eng: Engine, history: list[dict], user_text: str) -> list[dict]:
    """Mirror ``companion/round.py``: static system prefix, prior turns, then the
    newest user turn rebuilt with the felt block riding it."""
    history = history + [{"role": "user", "content": user_text}]
    eng.tick(history)
    injected = eng.inject(user_text)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        *history[:-1],
        {"role": "user", "content": injected},
    ]


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="feltstate_shapes_"))
    eng = Engine(
        source=KeywordSource(),
        state_path=tmp / "state.json",
        persona="Ivy — a quiet research assistant",
        dials=PersonaDials(warmth=0.65),
    )

    # ---- Moment A: the first morning ------------------------------------- #
    msgs = assemble(eng, [], "morning - first day trying this out. glad you're here.")
    show("A. the first morning - a fresh state, one warm turn", msgs)

    # ---- Moment B: back after three days ---------------------------------- #
    # Simulate elapsed time the same way the guided tour does: move the
    # last-contact clock three days back. Everything else is untouched.
    eng._last_user_ts = (datetime.now().astimezone() - timedelta(days=3)).isoformat()
    history = [
        {"role": "user", "content": "morning - first day trying this out. glad you're here."},
        {"role": "assistant", "content": "[content] Good to meet you. I keep notes."},
    ]
    msgs = assemble(eng, history, "hey, I'm back. long week.")
    show("B. back after three days - the block opens with the felt gap", msgs)

    # ---- Moment C: a hard evening — released vs suppressed ---------------- #
    # State prepared programmatically to show these exact shapes (a live run
    # reaches them through accumulated turns; see docs/INTEGRATION.md #3).
    eng._last_user_ts = None
    eng._since_phrase_at_anchor = None  # moment C stands alone; drop B's gap
    eng.state.pressure.bars.sadness = 0.55
    eng.state.pressure.phase = "releasing"
    eng.state.pressure.release_type = "tears"
    eng.state.mood.labels = ["sad"]
    eng.state.mood.valence = -0.30
    eng.state.mood.arousal = 0.35
    eng.state.mood.aftertaste = {"valence": -0.35, "arousal": 0.3, "weight": 0.5}

    injected = eng.inject("...today went badly. the launch slipped again.")
    show(
        "C-i. a hard evening, feelings let OUT (release_type='tears')",
        [{"role": "user", "content": injected}],
    )

    # The same moment with the release channel suppressed: what changes is one
    # line - the felt texture flips from letting the feeling out to holding it
    # in. (The channel is chosen by appraisal power: perceived control decides
    # express vs suppress; see feltstate.affect.compute_power.)
    eng.state.pressure.release_type = "tears_suppress"
    injected = eng.inject("...today went badly. the launch slipped again.")
    show(
        "C-ii. the same evening, feelings held IN (release_type='tears_suppress')",
        [{"role": "user", "content": injected}],
    )

    print("\n" + "=" * 72)
    print("Same persona, same code path - the injected block is a pure function")
    print("of persisted state. The system prompt never changed by a byte.")
    print("=" * 72)


if __name__ == "__main__":
    main()
