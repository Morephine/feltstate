#!/usr/bin/env python3
"""nightly — one whole day, digested while the agent sleeps.

Run it directly, no setup, no network, no dependencies beyond the standard
library and feltstate itself::

    python examples/nightly.py

The nightly pass is the loop that turns a day of talk into memory. The library
ships every stage as a composable piece; this example wires them in the order a
real deployment runs them after midnight:

1. **Read the day.** The transcript is the ground truth; nothing edits it.
2. **Distill facts.** A ``FactExtractor`` proposes 5W1H facts. Here it is a
   deliberately crude rule table — this seat is where your LLM goes. It reads
   the *whole day* before writing, so each ``why`` can be a real cause, not a
   per-message guess.
3. **Imprint keys.** Single words, chosen with the ledger's existing
   vocabulary in view (``key_vocab``) so new facts reuse old words and
   actually collide. This naming seat is also yours; the imprint rules
   (words only, deduplicated) are enforced mechanically either way.
4. **Judge kinship.** ``digest_canon`` gives each newborn its one collision
   pass; a judge decides which candidates become ``relates`` edges.
5. **Age and compact.** Salience decays on its own clock; what has gone dim
   moves to the archive sidecar; the main file stays lean.
6. **Report.** A night report you could read over coffee.

Everything is offline and deterministic in content (timestamps are run time).
"""

from __future__ import annotations

import json
import sys
import tempfile
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from feltstate.memory.canon import Canon  # noqa: E402
from feltstate.memory.extract import FactExtractor, commit_to_canon  # noqa: E402
from feltstate.memory.keyweb import (  # noqa: E402
    SharedKeyJudge,
    digest_canon,
    imprint_into,
    key_vocab,
)

# --------------------------------------------------------------------------- #
# The day being digested — a transcript, verbatim, untouched.                 #
# --------------------------------------------------------------------------- #
DAY = [
    {"role": "user", "content": "The landlord called back — the rent freeze is official!"},
    {"role": "assistant", "content": "That dispute took a month. Worth celebrating."},
    {"role": "user", "content": "Celebrated too hard: knocked the kettle off the counter."},
    {"role": "user", "content": "Kay came by in the evening and helped me fix the garden fence."},
    {"role": "assistant", "content": "Kay always shows up when it matters."},
    {"role": "user", "content": "Honestly the fence looks better than before it broke."},
]


class RuleExtractor(FactExtractor):
    """The distillation seat, filled by a rule table so the example runs offline.

    A real deployment puts an LLM here and asks it to read the whole day and
    propose the few events that actually happened, each with a *why* written
    from full-day context. The output contract is the same either way: plain
    dicts shaped for ``Canon.add``.
    """

    TABLE = [
        (
            "rent freeze is official",
            dict(
                actor="ash",
                action="won",
                object="the rent freeze became official",
                why="a month of dispute with the landlord finally paid off",
                intensity=0.85,
            ),
        ),
        (
            "knocked the kettle",
            dict(
                actor="ash",
                action="broke",
                object="knocked the kettle off the counter",
                why="over-celebrated the rent news",
                intensity=0.35,
            ),
        ),
        (
            "fix the garden fence",
            dict(
                actor="Kay",
                action="helped",
                object="helped fix the garden fence",
                why="came by unasked, the evening of the rent news",
                intensity=0.7,
            ),
        ),
    ]

    def extract(self, messages: Sequence[dict], *, actor_hint: str = "user") -> list[dict]:
        text = " ".join(m.get("content", "") for m in messages)
        return [dict(fact) for marker, fact in self.TABLE if marker in text]


# The naming seat: same story. An LLM (shown the ledger's working vocabulary)
# picks a handful of single words per fact; the imprint rules stay mechanical.
KEY_TABLE = {
    "the rent freeze became official": ["rent", "landlord", "dispute"],
    "knocked the kettle off the counter": ["kettle"],
    "helped fix the garden fence": ["garden", "fence", "kay"],
}


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "canon.jsonl"

        # Season the ledger with an older week so tonight's newborns have
        # something to collide with — and something dim enough to fade out.
        old = datetime.now(timezone.utc) - timedelta(days=9)
        seasons = [
            {
                "who": {"actor": "ash"},
                "what": {"action": "opened", "object": "dispute with the landlord"},
                "why": "the rent went up",
                "intensity": 0.8,
                "confidence": 0.9,
                "ts": old.isoformat(),
                "valid_at": old.isoformat(),
                "keys": ["rent", "landlord", "dispute"],
            },
            {
                "who": {"actor": "ash"},
                "what": {"action": "planted", "object": "tomatoes by the garden fence"},
                "why": "",
                "intensity": 0.55,
                "confidence": 0.9,
                "ts": old.isoformat(),
                "valid_at": old.isoformat(),
                "keys": ["garden", "tomato"],
            },
            {
                "who": {"actor": "ash"},
                "what": {"action": "noted", "object": "a passing thought about socks"},
                "why": "",
                "intensity": 0.12,
                "confidence": 0.9,
                "ts": old.isoformat(),
                "valid_at": old.isoformat(),
                "keys": ["socks"],
            },
        ]
        path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in seasons) + "\n", encoding="utf-8"
        )
        canon = Canon(path)

        print("== 1. the day, verbatim ==")
        for m in DAY:
            print(f"   {m['role']:>9}: {m['content']}")

        print("\n== 2. distilled: the whole day, read before writing ==")
        facts = RuleExtractor().extract(DAY, actor_hint="ash")
        stored = commit_to_canon(facts, canon, grey_zone=False)
        for s in stored:
            print(f"   + [{s['actor']}] {s['action']}: {s['object']}\n     why: {s['why']}")

        print("\n== 3. keys imprinted, old vocabulary in view ==")
        print("   ledger already speaks:", ", ".join(key_vocab(canon._load_confirmed())))
        for s in stored:
            keys = KEY_TABLE.get(s["object"], [])
            imprint_into(canon, s["id"], keys)
            print(f"   {s['object'][:40]:41s} <- {' '.join(keys)}")

        print("\n== 4. kinship judged: one collision pass per newborn ==")
        report = digest_canon(canon, [s["id"] for s in stored], judge=SharedKeyJudge(min_shared=1))
        print(
            f"   candidates admitted: {report['candidates']}, edges written: {len(report['edges'])}"
        )
        rows = {r.get("what", {}).get("object"): r for r in canon._load_confirmed()}
        for obj, row in rows.items():
            rel = (row.get("fp") or {}).get("relates") or row.get("relates") or []
            if rel:
                print(f"   {obj[:44]:45s} · {len(rel)} kin")

        print("\n== 5. age and compact: what has gone dim leaves the main file ==")
        before = sum(1 for _ in open(path, encoding="utf-8"))
        canon.compact()
        after = sum(1 for _ in open(path, encoding="utf-8"))
        print(f"   main store rows: {before} -> {after} (dim rows now live in the archive sidecar)")

        print("\n== 6. night report ==")
        print(
            f"   distilled {len(stored)} facts · keyed {len(stored)} · edges {len(report['edges'])}"
        )
        print("   the day is digested; tomorrow's newborns will collide against it.")


if __name__ == "__main__":
    main()
