"""Tests for feltstate.memory.keyweb — word keys, the collision pass, judged edges.

The behaviours pinned here:

* keys are words: phrases, clause punctuation, and over-long strings are
  rejected at imprint time;
* relevance approaches its ceiling asymptotically (1 key -> 1.0x, never 2.0x);
* the admission floor rises with the age gap (ten years ≈ 0.95 with defaults)
  and prices **birth** intensity, not decayed intensity;
* a newcomer collides against the whole ledger but only looks *backward*;
* the digest gives each newcomer exactly one protagonist pass, writes judged
  edges on **both** rows, and writes nothing without a judge;
* :func:`digest_canon` persists edges transactionally on a live Canon.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from feltstate import Canon
from feltstate.memory.keyweb import (
    KeyWebConfig,
    SharedKeyJudge,
    admission_floor,
    collide,
    day_digest,
    digest_canon,
    imprint_keys,
    relevance_mult,
)


def _entry(actor, obj, *, intensity=0.6, days_ago=0.0, keys=()):
    ts = (datetime.now(timezone.utc).astimezone() - timedelta(days=days_ago)).isoformat()
    e = {
        "who": {"actor": actor},
        "what": {"action": "noted", "object": obj},
        "intensity": intensity,
        "ts": ts,
        "valid_at": ts,
    }
    if keys:
        imprint_keys(e, keys)
    return e


def test_imprint_rejects_phrases_and_dedups():
    e = {}
    kept = imprint_keys(
        e,
        [
            "rent",
            "rent",  # duplicate
            "RENT",  # case-duplicate
            "the rent went up",  # phrase: whitespace
            "rent,dispute",  # clause punctuation
            "x" * 40,  # over-long
            "dispute",
        ],
    )
    assert kept == ["rent", "dispute"]
    assert e["keys"] == ["rent", "dispute"]
    # No surviving keys -> no field written at all.
    e2 = {}
    assert imprint_keys(e2, ["not a key either"]) == []
    assert "keys" not in e2


def test_relevance_ceiling_is_asymptotic():
    assert relevance_mult(0) == 0.0
    assert relevance_mult(1) == 1.0  # touching once is not relevance
    assert relevance_mult(2) == 1.5
    assert relevance_mult(4) == 1.75
    assert relevance_mult(1000) < 2.0  # the ceiling is never reached


def test_admission_floor_rises_with_age_gap():
    assert admission_floor(0.0) == 0.0
    one, five, ten = admission_floor(1.0), admission_floor(5.0), admission_floor(10.0)
    assert 0.2 < one < 0.3
    assert 0.7 < five < 0.85
    assert 0.94 < ten < 0.96  # the ten-year ≈ 0.95 calibration
    assert admission_floor(-3.0) == 0.0  # younger candidates pay no floor


def test_collide_looks_backward_and_prices_birth_intensity():
    old_strong = _entry("kai", "the rent dispute", intensity=0.8, days_ago=400, keys=["rent", "dispute"])
    old_faint = _entry("kai", "rent passing mention", intensity=0.15, days_ago=400, keys=["rent", "dispute"])
    newer = _entry("kai", "tomorrow's fact", intensity=0.9, days_ago=-2, keys=["rent"])
    nb = _entry("kai", "rent came up again", intensity=0.5, keys=["rent", "dispute"])
    ledger = [old_strong, old_faint, newer, nb]

    reports = collide(nb, ledger)
    touched = {r["entry"]["what"]["object"]: r for r in reports}

    assert "tomorrow's fact" not in touched  # backward-looking only
    # Both old rows share 2 keys -> x1.5. Floor at ~1.1y ≈ 0.28.
    assert touched["the rent dispute"]["admitted"] is True  # 0.8*1.5=1.2 >= floor
    assert touched["rent passing mention"]["admitted"] is False  # 0.15*1.5=0.225 < floor ~0.28
    assert touched["the rent dispute"]["hits"] == 2


def test_day_digest_writes_edges_on_both_rows_only_with_a_judge():
    old = _entry("kai", "the rent dispute", intensity=0.8, days_ago=30, keys=["rent", "dispute"])
    nb = _entry("kai", "rent resolved at last", intensity=0.7, keys=["rent", "dispute"])
    ledger = [old, nb]

    dry = day_digest([nb], ledger, judge=None)
    assert dry["passes"] == 1 and dry["candidates"] == 1 and dry["edges"] == []
    assert "relates" not in nb and "relates" not in old  # no judge, no edges

    wet = day_digest([nb], ledger, judge=SharedKeyJudge(min_shared=2))
    assert len(wet["edges"]) == 1
    assert nb["relates"][0]["to"] and old["relates"][0]["to"]
    assert nb["relates"][0]["why"].startswith("shared keys:")
    # Re-running the digest does not duplicate the edge.
    again = day_digest([nb], ledger, judge=SharedKeyJudge(min_shared=2))
    assert again["edges"] == []
    assert len(nb["relates"]) == 1 and len(old["relates"]) == 1


def test_digest_canon_persists_edges_transactionally(tmp_path):
    path = tmp_path / "canon.jsonl"
    c = Canon(path)
    old = _entry("kai", "the rent dispute", intensity=0.8, days_ago=30, keys=["rent", "dispute"])
    nb = _entry("kai", "rent resolved at last", intensity=0.7, keys=["rent", "dispute"])
    path.write_text(json.dumps(old) + "\n" + json.dumps(nb) + "\n", encoding="utf-8")

    from feltstate.memory.canon import _entry_id

    report = digest_canon(c, [_entry_id(nb)], judge=SharedKeyJudge(min_shared=2))
    assert len(report["edges"]) == 1

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    by_obj = {r["what"]["object"]: r for r in rows}
    assert by_obj["rent resolved at last"]["relates"][0]["to"] == _entry_id(old)
    assert by_obj["the rent dispute"]["relates"][0]["to"] == _entry_id(nb)
