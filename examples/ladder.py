#!/usr/bin/env python3
"""ladder — watch a season of days become a month, one melt at a time.

Run it directly, no setup, no network, no dependencies beyond the standard
library and feltstate itself::

    python examples/ladder.py

The crystallisation ladder is how a companion's memory stays *lifetime-sized*:
days that mattered are cast into day crystals; a tier's crystals cluster by
their shared keys and, once a theme has accumulated enough, melt one rung up.
Melted members sink — still on disk, still drillable, never melted twice —
and the fused crystal takes a small climb bonus: surviving the melt is
evidence of mattering.

Dials here are shrunk (3 days -> a week, 2 weeks -> a month) so one run
climbs two rungs. Production-shaped defaults live in ``DEFAULT_LADDER``.
Everything below is offline and deterministic.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from feltstate.memory.ladder import (  # noqa: E402
    TierDial,
    absorbed_mids,
    cast_day_crystals,
    ladder_pass,
    load_crystals,
)

T0 = datetime(2026, 3, 2, 20, 0, tzinfo=timezone.utc)


def fact(i: int, obj: str, keys: list[str], inten: float = 0.8) -> dict:
    when = (T0 + timedelta(days=i)).isoformat()
    return {
        "id": f"f{i}",
        "intensity": inten,
        "valid_at": when,
        "who": {"actor": "ash"},
        "what": {"action": "noted", "object": obj},
        "why": "",
        "keys": keys,
        "valence": 0.0,
    }


def main() -> None:
    days = [
        # a rent saga, three "weeks" of it
        *[fact(i, f"rent skirmish, day {i}", ["rent", "landlord"]) for i in range(3)],
        *[fact(3 + i, f"rent talks, day {3 + i}", ["rent", "landlord"]) for i in range(3)],
        *[fact(6 + i, f"rent settled, day {6 + i}", ["rent", "landlord"]) for i in range(3)],
        # and a quieter garden thread that hasn't earned a melt yet
        fact(9, "planted tomatoes", ["garden"]),
        fact(10, "fixed the fence with Kay", ["garden", "fence"]),
    ]

    with tempfile.TemporaryDirectory() as td:
        store = Path(td) / "crystals.jsonl"
        dials = (
            TierDial("day", "week", batch=3, min_heat=0.4, half_life_days=90.0),
            TierDial("week", "month", batch=2, min_heat=0.4, half_life_days=180.0),
        )
        now = T0 + timedelta(days=12)

        born = cast_day_crystals(days, store, floor=0.7, now=now)
        print(f"== cast: {len(born)} day crystals (the edges of {len(days)} days) ==")

        for n in (1, 2):
            rep = ladder_pass(store, dials=dials, now=now)
            print(f"\n== ladder pass {n} ==")
            for c in rep["melted"]:
                print(f"   melt -> [{c['tier']}] heat {c['heat']}  keys {' '.join(c['keys'])}")
                print(f"           from {len(c['src_ids'])} members")
            if not rep["melted"]:
                print("   nothing ready to climb")
            if rep["waiting"]:
                print(f"   waiting: {rep['waiting']}")

        crystals = load_crystals(store)
        gone = absorbed_mids(crystals)
        month = next(c for c in crystals if c["tier"] == "month")
        print("\n== the store, tier by tier ==")
        for tier in ("day", "week", "month"):
            live = [c for c in crystals if c["tier"] == tier and c["mid"] not in gone]
            sunk = [c for c in crystals if c["tier"] == tier and c["mid"] in gone]
            print(f"   {tier:>5}: {len(live)} live, {len(sunk)} absorbed (sunk, still drillable)")

        print("\n== drill: the month crystal remembers where it came from ==")
        print(f"   [month] {month['text'][:70]}…")
        for wk_mid in month["src_ids"]:
            wk = next(c for c in crystals if c["mid"] == wk_mid)
            print(f"     └─ [week] {wk['text'][:56]}…")
            for day_mid in wk["src_ids"][:2]:
                dy = next(c for c in crystals if c["mid"] == day_mid)
                print(f"          └─ [day] {dy['text']}")
            if len(wk["src_ids"]) > 2:
                print(f"          └─ … {len(wk['src_ids']) - 2} more days")

        print("\nA lifetime stays scannable because the ladder keeps the live set")
        print("small — and every rung above still answers for the words below.")


if __name__ == "__main__":
    main()
