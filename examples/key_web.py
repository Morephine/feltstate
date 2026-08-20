#!/usr/bin/env python3
"""key_web — watch a ledger of facts become a web, then read the web back.

Run it directly, no setup, no network, no dependencies beyond the standard
library and feltstate itself::

    python examples/key_web.py

What it demonstrates
--------------------
* **Keys are words, born on the row.** Each fact is imprinted with a handful
  of single-word keys at write time — a phrase never collides, so phrases are
  rejected mechanically. ``key_vocab`` shows the extractor which words the
  ledger already speaks, so new facts prefer old words and actually meet.
* **Edges are judged, not assumed.** A nightly-style digest gives each new
  fact one collision pass against the whole ledger; shared words make
  *candidates*, and only a judge's verdict writes a ``relates`` edge — carried
  on **both** rows, with the *why* it was bound.
* **Reading is the same web backwards.** ``Canon.reach`` enters by colliding
  query words with keys, gathers kin along judged edges, and orders the
  gathered facts by event time. **The chain's tail is the present** — no
  invalidation flags, no semantic index: the newest first-hand fact wins by
  standing last.

Everything is deterministic: fixed event dates, the zero-dependency
``SharedKeyJudge``, and a temporary store that is deleted on exit.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from feltstate.memory.canon import Canon, _entry_id  # noqa: E402
from feltstate.memory.keyweb import (  # noqa: E402
    SharedKeyJudge,
    digest_canon,
    imprint_keys,
    key_vocab,
)


def fact(actor: str, obj: str, when: str, intensity: float, keys: list[str]) -> dict:
    e = {
        "who": {"actor": actor},
        "what": {"action": "noted", "object": obj},
        "intensity": intensity,
        "confidence": 0.9,
        "ts": when,
        "valid_at": when,
    }
    imprint_keys(e, keys)
    return e


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "canon.jsonl"

        # A month of one small drama, plus unrelated noise. Keys are words —
        # note "the rent went up" would be rejected; "rent" collides.
        rows = [
            fact("ash", "the rent went up", "2026-03-02T19:40:00+08:00", 0.9, ["rent", "money"]),
            fact(
                "ash",
                "dispute opened with the landlord",
                "2026-03-09T21:05:00+08:00",
                0.9,
                ["dispute", "landlord", "money"],
            ),
            fact("ash", "bought a kettle", "2026-03-11T15:00:00+08:00", 0.5, ["kettle"]),
            fact(
                "ash",
                "landlord agreed to freeze the rent",
                "2026-03-28T20:30:00+08:00",
                0.9,
                ["rent", "landlord"],
            ),
        ]
        path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
        )
        canon = Canon(path)

        print("== the ledger's working vocabulary (feed this to your extractor) ==")
        print("   " + ", ".join(key_vocab(rows)))

        # The digest: each newcomer gets its one protagonist pass; the judge
        # binds kin and the edge lands on both rows with its why.
        newcomers = [r for r in rows if r["what"]["object"] != "the rent went up"]
        report = digest_canon(
            canon, [_entry_id(r) for r in newcomers], judge=SharedKeyJudge(min_shared=1)
        )
        print("\n== digest: judged edges written on both rows ==")
        for edge in report["edges"]:
            print(f"   -> {edge['to'][:10]}…  because: {edge['why']}")

        # The query leg. "rent" collides with two facts; the dispute enters as
        # kin along a judged edge; the kettle never shows up.
        answer = canon.reach("rent")
        print('\n== reach("rent") — the chain, oldest to newest ==')
        for f in answer["chain"]:
            how = (
                f"key hit ({', '.join(f['shared'])})"
                if f["entered"] == "key"
                else f"kin via edge: {f['via']}"
            )
            print(f"   [{f['valid_at'][:10]}] {f['object']}   <- {how}")
        print(f"\n   current (the tail): {answer['current']['object']}")
        print("   — nothing was marked invalid; the newer fact simply stands last.")


if __name__ == "__main__":
    main()
