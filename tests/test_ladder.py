"""Tests for feltstate.memory.ladder — casting, climbing, and derived state.

The behaviours pinned here:

* casting takes *birth* intensity over a floor, once per fact — "already
  cast" is derived from the store's own ``src_ids``, never marked;
* absorption is likewise derived: a crystal cited above never melts again;
* a rung admits by *current* heat (pure-function decay), so a cold member
  waits while a warm one climbs;
* the fused crystal takes the climb bonus, unions its members' keys, and
  keeps full ``src_ids`` lineage;
* clusters below batch size are not errors — they wait.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from feltstate.memory.ladder import (
    TierDial,
    absorbed_mids,
    cast_day_crystals,
    cast_fact_ids,
    cluster_by_key,
    heat_now,
    ladder_pass,
    load_crystals,
)

NOW = datetime(2026, 3, 30, 12, 0, tzinfo=timezone.utc)


def _fact(fid, obj, inten=0.8, keys=("rent",), when=None):
    when = (when or NOW).isoformat()
    return {
        "id": fid,
        "intensity": inten,
        "valid_at": when,
        "who": {"actor": "ash"},
        "what": {"action": "noted", "object": obj},
        "why": "",
        "keys": list(keys),
        "valence": 0.0,
    }


def test_cast_takes_floor_and_never_recasts(tmp_path):
    store = tmp_path / "c.jsonl"
    rows = [_fact("a", "one"), _fact("b", "two"), _fact("dim", "meh", inten=0.2)]
    born = cast_day_crystals(rows, store, floor=0.7, now=NOW)
    assert [c["src_ids"] for c in born] == [["a"], ["b"]]  # the dim fact stays uncast
    assert cast_day_crystals(rows, store, floor=0.7, now=NOW) == []  # derived, not marked
    assert cast_fact_ids(load_crystals(store)) == {"a", "b"}


def test_ladder_melts_a_theme_and_absorption_is_derived(tmp_path):
    store = tmp_path / "c.jsonl"
    rows = [_fact(f"r{i}", f"rent day {i}") for i in range(3)] + [
        _fact("k", "kettle day", keys=("kettle",))
    ]
    cast_day_crystals(rows, store, floor=0.7, now=NOW)
    dials = (TierDial("day", "week", batch=3, min_heat=0.5, half_life_days=30.0),)
    rep = ladder_pass(store, dials=dials, now=NOW)
    assert len(rep["melted"]) == 1
    week = rep["melted"][0]
    assert week["tier"] == "week"
    assert sorted(week["src_ids"]) == ["day-r0", "day-r1", "day-r2"]
    assert week["keys"] == ["rent"]
    # the kettle singleton waits; the melted members are absorbed by derivation
    assert rep["waiting"]["day"] == 1
    assert absorbed_mids(load_crystals(store)) == {"day-r0", "day-r1", "day-r2"}
    rep2 = ladder_pass(store, dials=dials, now=NOW)
    assert rep2["melted"] == []  # melted material never re-enters the furnace


def test_admission_is_current_heat_not_born_heat(tmp_path):
    store = tmp_path / "c.jsonl"
    old = NOW - timedelta(days=120)  # four half-lives at 30d -> heat ~6% of born
    rows = [
        _fact("cold", "long ago", when=old),
        _fact("w1", "warm one"),
        _fact("w2", "warm two"),
        _fact("w3", "warm three"),
    ]
    cast_day_crystals(rows, store, floor=0.7, now=NOW)
    dials = (TierDial("day", "week", batch=3, min_heat=0.5, half_life_days=30.0),)
    rep = ladder_pass(store, dials=dials, now=NOW)
    assert len(rep["melted"]) == 1
    assert "day-cold" not in rep["melted"][0]["src_ids"]  # too cold to climb


def test_climb_bonus_and_heat_now_math(tmp_path):
    store = tmp_path / "c.jsonl"
    rows = [_fact(f"r{i}", f"rent {i}", inten=0.8) for i in range(3)]
    cast_day_crystals(rows, store, floor=0.7, now=NOW)
    day = load_crystals(store)[0]
    assert heat_now(day, NOW, half_life_days=30.0) == day["heat"]  # no age, no decay
    half = heat_now(day, NOW + timedelta(days=30), half_life_days=30.0)
    assert abs(half - day["heat"] / 2) < 1e-9
    dials = (TierDial("day", "week", batch=3, min_heat=0.5, climb_bonus=0.08),)
    week = ladder_pass(store, dials=dials, now=NOW)["melted"][0]
    assert week["heat"] <= 1.0
    assert week["heat"] > day["heat"]  # climbing pays


def test_cluster_by_key_prefers_the_most_shared_word():
    members = [
        {"mid": "1", "keys": ["rent", "landlord"]},
        {"mid": "2", "keys": ["rent"]},
        {"mid": "3", "keys": ["garden"]},
        {"mid": "4", "keys": ["garden", "fence"]},
        {"mid": "5", "keys": ["socks"]},
    ]
    clusters = cluster_by_key(members)
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [1, 2, 2]  # rent pair, garden pair, socks alone — waiting, not wrong
