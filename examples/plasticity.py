#!/usr/bin/env python3
"""plasticity — what fires, sensitizes; what is safe, heals.

Run it::

    python examples/plasticity.py

Every tick whose raw inflow clears a charge threshold nudges that bar's
*sensitivity* up by a micro amount (~1e-5 and down); inflow is then multiplied
by ``1 + k x (sensitivity - 0.5)``. What life keeps hitting becomes genuinely
easier to stir — and it heals back toward 0.5 a fraction of a percent per day,
paced by ``relationship.safety``. Nothing here is perceptible in a day; the
point is the **shape that half a year leaves**.

This demo runs the same 180 days for two characters who differ in exactly one
dial-of-life: how safe the bond is. 120 days of cheerful daily chatter (60
joy-charged ticks a day), one betrayal on day 90, then 60 days of quiet.
Deterministic: fixed clock, no randomness.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, __import__("pathlib").Path(__file__).resolve().parent.parent.as_posix())

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from feltstate.affect.pressure import _plast_gain, step  # noqa: E402
from feltstate.config import PersonaDials, PressureConfig  # noqa: E402
from feltstate.state import AffectDelta, PressureState, Relationship, Traits  # noqa: E402

CFG = PressureConfig()
T0 = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)


def tick(p: PressureState, rel: Relationship, when: datetime, delta: AffectDelta) -> None:
    step(
        p,
        delta=delta,
        traits=Traits(),
        relationship=rel,
        dials=PersonaDials(),
        cfg=CFG,
        ts=when.isoformat(),
        elapsed_ticks=1.0,
    )


def sens(p: PressureState, bar: str) -> float:
    return p.sensitivity.get(bar, 0.5)


def live_a_half_year(safety: float) -> PressureState:
    p = PressureState()
    rel = Relationship(safety=safety)
    day = T0
    for d in range(180):
        if d < 120:  # chatty months: 60 joy-charged ticks a day
            for i in range(60):
                tick(p, rel, day + timedelta(minutes=5 * i), AffectDelta(labels=["joyful"]))
        else:  # quiet months: one silent tick a day (healing still runs)
            tick(p, rel, day, AffectDelta())
        if d == 90:  # one betrayal, in the middle of the good stretch
            tick(
                p,
                rel,
                day + timedelta(hours=12),
                AffectDelta(milestones=[{"kind": "trauma_betrayal", "severity": 1.0}]),
            )
        day += timedelta(days=1)
    return p


def report(label: str, p: PressureState) -> None:
    print(f"\n{label}")
    print(f"  joy     sensitivity {sens(p, 'joy'):.5f}   gain x{_plast_gain(p, 'joy', CFG):.4f}")
    print(
        f"  sadness sensitivity {sens(p, 'sadness'):.5f}   gain x{_plast_gain(p, 'sadness', CFG):.4f}"
    )
    print(
        f"  anger   sensitivity {sens(p, 'anger'):.5f}   gain x{_plast_gain(p, 'anger', CFG):.4f}"
    )


def main() -> None:
    print("=" * 68)
    print("plasticity — the same 180 days, two safeties")
    print("=" * 68)
    print(
        "120 chatty days (60 joy ticks/day), one betrayal on day 90,\n"
        "then 60 quiet days. Only relationship.safety differs."
    )

    safe = live_a_half_year(safety=0.9)
    wary = live_a_half_year(safety=0.1)

    report("safety 0.9 — a settled bond (carves the same, heals fast)", safe)
    report("safety 0.1 — a wary bond (carves the same, heals slow)", wary)

    print()
    print("What to read off the numbers:")
    print("- joy is the most-carved dimension: seven thousand small hits, not one")
    print("  big one. The betrayal registered exactly one heavy hit — a single")
    print("  bad day cannot bend a character.")
    print("- both lived identical days; they differ only in how fast the carving")
    print("  relaxes. Safety is the healing rate, not the experience.")
    print("- gains still round to ~x1.0 — half a year is meant to move the")
    print("  needle by a hair. Character change runs on a 180-day scale.")


if __name__ == "__main__":
    main()
