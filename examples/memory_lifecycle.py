#!/usr/bin/env python3
"""memory_lifecycle — watch a memory get born with evidence, grow old, and die
for real.

Run it directly, no setup, no network, no dependencies beyond the standard
library and feltstate itself::

    python examples/memory_lifecycle.py

What it demonstrates
--------------------
* **Born with evidence.** Every memory is minted with a birth fingerprint —
  pointers at the exact source text, the affect estimate for the moment (produced by the
  same engine the rest of feltstate uses, not self-reported), and a UTC
  timestamp — sealed into a content id, so edits to the record are detectable.
  Its *identity* is a separate unique id, so two memories that happen to hash
  alike are never confused (or deleted together).
* **Ages on its own clock.** A distilled life-lesson, a warm imprint and a
  plain fact decay at different gears; nothing has a hidden floor.
* **Dies with authority and manners.** The collector never touches what it
  cannot *verifiably* trace, a living distilled memory shields the facts it
  grew from (transitively), and heritage is not life-support: when a fused
  memory's ancestor dies, the branch is pruned and the fusion lives on as an
  *instinct memory*.
* **Gone from store and snapshot alike.** The reaper runs one transaction:
  tombstone first, then remove the row from the live store *and* the backup
  snapshot it is given, replayable after a crash.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from feltstate.engine import Engine
from feltstate.memory.lifecycle import (
    Chain,
    ClockConfig,
    current_intensity,
    drill,
    execute,
    fuse,
    leaf_pointers,
    make_fingerprint,
    make_source_ptr,
    resolve_deaths,
    trace_contexts,
    verify_fingerprint,
    verify_source_ptr,
)
from feltstate.sources.keyword import KeywordSource

TS = "2026-01-10T21:00:00+00:00"


def flat_affect(engine: Engine) -> dict:
    st = engine.state
    return {"valence": round(st.mood.valence, 4), "arousal": round(st.mood.arousal, 4)}


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="feltstate_lifecycle_"))
    store, snap = work / "memories.jsonl", work / "snapshot.jsonl"
    ledger, pending = work / "ledger.jsonl", work / "pending.json"

    # 1. A moment happens; the engine feels it; a memory is born sealed.
    engine = Engine(source=KeywordSource(), state_path=str(work / "state.json"))
    engine.tick(
        [
            {
                "role": "user",
                "content": "we finally fixed it together, thank you — that was wonderful",
            }
        ]
    )
    affect = flat_affect(engine)

    raw = work / "raw_2026-01-10.md"
    line1 = "21:00 user: we finally fixed it together, thank you\n"
    line2 = "21:01 agent: took us all evening — worth it\n"
    raw.write_text(line1 + line2, encoding="utf-8")
    ptr_a = make_source_ptr(raw.name, "21:00", "21:00", line1)
    ptr_b = make_source_ptr(raw.name, "21:01", "21:01", line2)

    fact_a = {
        "cid": "fact-evening",
        "kind": "fact",
        "base": 0.45,
        "text": "they fixed the bug together late in the evening",
        "fp": make_fingerprint([ptr_a], affect, TS, mid="fact-evening"),
    }
    fact_b = {
        "cid": "fact-thanks",
        "kind": "fact",
        "base": 0.4,
        "text": "the user said thank you, warmly",
        "fp": make_fingerprint([ptr_b], affect, TS, mid="fact-thanks"),
    }
    print("(1) born with evidence:")
    print(
        f"    fp_id={fact_a['fp']['fp_id'][:16]}...  mid={fact_a['fp']['mid']}  "
        f"verified={verify_fingerprint(fact_a['fp'])}"
    )
    forged = json.loads(json.dumps(fact_a["fp"]))
    forged["core"]["birth_affect"]["valence"] = 0.99
    print(f"    tamper with the recorded feeling -> verified={verify_fingerprint(forged)}")

    # 2. Distillation and fusion — the lesson outranks its facts.
    lesson = {
        "cid": "lesson-repair",
        "kind": "distilled",
        "base": 0.7,
        "text": "hard evenings end well when they debug together",
        "fp": fuse([fact_a["fp"], fact_b["fp"]], affect, TS, mid="lesson-repair"),
    }
    lesson["fp"]["src"] = [fact_a["fp"]["mid"]]  # life-support: shields fact A
    print(
        f"\n(2) fused into a distilled lesson "
        f"(carries {len(lesson['fp']['core']['source_ptrs'])} copied source ptrs, "
        f"lineage={len(lesson['fp']['lineage'])})"
    )

    # 2b. Drill the lesson back through its genealogy and then into transcript
    # context. The transcript store is still the application's; here the loader
    # is just an in-memory mapping for the demo.
    fp_store = {
        fact_a["fp"]["mid"]: fact_a["fp"],
        fact_b["fp"]["mid"]: fact_b["fp"],
    }
    tree = drill(lesson["fp"], fp_store.get)
    pointers = leaf_pointers(tree)
    turns = [
        {"role": "user", "content": line1.strip(), "timestamp": "21:00"},
        {"role": "assistant", "content": line2.strip(), "timestamp": "21:01"},
    ]
    contexts = trace_contexts(tree, lambda _file: turns, before=0, after=0)
    print(
        f"    drill -> leaves={len(pointers)}, resolved contexts="
        f"{sum(1 for item in contexts if item['context'].get('ok'))}"
    )
    print(f"    exact first source still verifies={verify_source_ptr(ptr_a, line1)}")

    legacy = {
        "cid": "legacy-note",
        "kind": "fact",
        "text": "an old note from before fingerprinting",
        "fp": {"backfill": "coarse", "mid": "legacy-note"},
    }

    rows = [fact_a, fact_b, lesson, legacy]
    for p in (store, snap):
        p.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
        )

    # 3. Fast-forward a year — different kinds age on different gears.
    cfg, days = ClockConfig(), 365.0
    print("\n(3) intensity after a year:")
    for r in (lesson, fact_a, fact_b):
        print(
            f"    {r['cid']:<14} {r['kind']:<9} {r['base']:.2f} -> "
            f"{current_intensity(r['base'], days, r['kind'], cfg):.3f}"
        )

    plan = resolve_deaths(
        rows, lambda m: current_intensity(m.get("base", 0.3), days, m.get("kind", "fact"), cfg)
    )
    print(
        f"\n(4) the judge rules: dead={plan['dead_ids']}  "
        f"legacy skipped={plan['skipped_legacy']}  prune={plan['prune']}"
    )
    print(
        "    (fact-evening faded too, but the living lesson shields it; "
        "fact-thanks has no protector)"
    )

    # 4. The reaper: one transaction — tombstone, store + snapshot purge, instinct.
    alarms: list[str] = []
    chain = Chain(ledger, [store], on_alarm=alarms.append)
    chain.patrol()
    execute(
        plan,
        stores=[store],
        ledger_path=ledger,
        pending_path=pending,
        snapshot_paths=[snap],
        txid="demo-tx",
        now_iso=TS,
    )

    left = [json.loads(x)["cid"] for x in store.read_text().splitlines()]
    snap_left = [json.loads(x)["cid"] for x in snap.read_text().splitlines()]
    lesson_now = next(
        json.loads(x)
        for x in store.read_text().splitlines()
        if json.loads(x)["cid"] == "lesson-repair"
    )
    print(f"\n(5) after the reaper: store={left}")
    print(f"    snapshot too: {snap_left}   (backups forget with the store)")
    print(
        f"    lesson lineage now={lesson_now['fp']['lineage']}, still carries "
        f"{len(lesson_now['fp']['core']['source_ptrs'])} source copies: an instinct memory"
    )

    r = chain.patrol()
    print(
        f"\n(6) the witness: lawful deaths={[k.split('::')[-1] for k in r['lawful_deaths']]}"
        f"  alarms={alarms}"
    )
    store.write_text("")
    chain.patrol()
    print(f"    evaporate the rest with no tombstone -> alarms={len(alarms)}")
    print(f"    full ledger still verifies: {chain.verify_full()}")


if __name__ == "__main__":
    main()
