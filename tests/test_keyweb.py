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
    old_strong = _entry(
        "ash", "the rent dispute", intensity=0.8, days_ago=400, keys=["rent", "dispute"]
    )
    old_faint = _entry(
        "ash", "rent passing mention", intensity=0.15, days_ago=400, keys=["rent", "dispute"]
    )
    newer = _entry("ash", "tomorrow's fact", intensity=0.9, days_ago=-2, keys=["rent"])
    nb = _entry("ash", "rent came up again", intensity=0.5, keys=["rent", "dispute"])
    ledger = [old_strong, old_faint, newer, nb]

    reports = collide(nb, ledger)
    touched = {r["entry"]["what"]["object"]: r for r in reports}

    assert "tomorrow's fact" not in touched  # backward-looking only
    # Both old rows share 2 keys -> x1.5. Floor at ~1.1y ≈ 0.28.
    assert touched["the rent dispute"]["admitted"] is True  # 0.8*1.5=1.2 >= floor
    assert touched["rent passing mention"]["admitted"] is False  # 0.15*1.5=0.225 < floor ~0.28
    assert touched["the rent dispute"]["hits"] == 2


def test_day_digest_writes_edges_on_both_rows_only_with_a_judge():
    old = _entry("ash", "the rent dispute", intensity=0.8, days_ago=30, keys=["rent", "dispute"])
    nb = _entry("ash", "rent resolved at last", intensity=0.7, keys=["rent", "dispute"])
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
    old = _entry("ash", "the rent dispute", intensity=0.8, days_ago=30, keys=["rent", "dispute"])
    nb = _entry("ash", "rent resolved at last", intensity=0.7, keys=["rent", "dispute"])
    path.write_text(json.dumps(old) + "\n" + json.dumps(nb) + "\n", encoding="utf-8")

    from feltstate.memory.canon import _entry_id

    report = digest_canon(c, [_entry_id(nb)], judge=SharedKeyJudge(min_shared=2))
    assert len(report["edges"]) == 1

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    by_obj = {r["what"]["object"]: r for r in rows}
    assert by_obj["rent resolved at last"]["relates"][0]["to"] == _entry_id(old)
    assert by_obj["the rent dispute"]["relates"][0]["to"] == _entry_id(nb)


def test_retracted_rows_neither_collide_nor_take_keys(tmp_path):
    """Dead rows are history, not memory (review fix): a retracted fact is no
    candidate, gains no edges, and refuses key imprinting via imprint_into."""
    import json

    from feltstate import Canon
    from feltstate.memory.keyweb import (
        SharedKeyJudge,
        _entry_id,
        digest_canon,
        imprint_into,
        imprint_keys,
    )

    c = Canon(tmp_path / "canon.jsonl")
    c.add("ash", "keeps a garden", intensity=0.6)
    c.retract("keeps a garden")
    c.add("ash", "waters the roses", intensity=0.6)

    def rows():
        text = (tmp_path / "canon.jsonl").read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    dead_id = _entry_id(next(r for r in rows() if r.get("_retracted")))
    assert imprint_into(c, dead_id, ["garden", "roses"]) is None  # dead rows take no keys

    # Seed keys on every raw row so the dead one *would* collide if it could.
    seeded = rows()
    for r in seeded:
        imprint_keys(r, ["garden", "roses"])
    (tmp_path / "canon.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in seeded) + "\n", encoding="utf-8"
    )

    newcomer_id = _entry_id(
        next(r for r in seeded if not r.get("_retracted") and "roses" in str(r.get("what")))
    )
    digest_canon(c, [newcomer_id], SharedKeyJudge())

    final = rows()
    dead = next(r for r in final if r.get("_retracted"))
    assert not dead.get("relates"), "retracted row must gain no edges"
    for r in final:
        assert dead_id not in [e["to"] for e in r.get("relates", [])], (
            "no edge may point at a retracted row"
        )


def test_no_duplicate_edges_when_ledger_holds_same_id_twice():
    """Review fix: two same-id rows in the pool yield one edge, not two."""
    from feltstate.memory.keyweb import SharedKeyJudge, _entry_id, day_digest, imprint_keys

    old_a = {
        "who": {"actor": "ash"},
        "what": {"object": "likes rain"},
        "intensity": 0.9,
        "ts": "2026-01-01T10:00:00+00:00",
        "valid_at": "2026-01-01T10:00:00+00:00",
    }
    old_b = dict(old_a)  # same (actor|object) -> same id
    nb = {
        "who": {"actor": "ash"},
        "what": {"object": "bought an umbrella"},
        "intensity": 0.8,
        "ts": "2026-01-02T10:00:00+00:00",
        "valid_at": "2026-01-02T10:00:00+00:00",
    }
    for r in (old_a, old_b, nb):
        imprint_keys(r, ["rain", "umbrella"])
    day_digest([nb], [old_a, old_b, nb], SharedKeyJudge())
    tos = [e["to"] for e in nb.get("relates", [])]
    assert tos.count(_entry_id(old_a)) == 1


def test_imprint_into_is_the_locked_write_path(tmp_path):
    """Review fix: keys reach a live Canon row through a supported, locked
    helper — validated as words, persisted, visible in the rendered view."""
    import json

    from feltstate import Canon
    from feltstate.memory.keyweb import imprint_into

    c = Canon(tmp_path / "canon.jsonl")
    view = c.add("ash", "keeps a garden")
    row = imprint_into(c, view["id"], ["garden", "not a key", "roses"])
    assert row is not None and row["keys"] == ["garden", "roses"]

    disk = json.loads((tmp_path / "canon.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert disk["keys"] == ["garden", "roses"]
    assert c.search("garden")[0]["keys"] == ["garden", "roses"]  # view passthrough

    assert imprint_into(c, "no-such-id", ["x"]) is None


def test_key_vocab_ranks_by_use_then_first_appearance():
    from feltstate.memory.keyweb import key_vocab

    ledger = [
        _entry("ash", "rent dispute opens", keys=("rent", "landlord")),
        _entry("ash", "rent dispute drags on", keys=("rent", "letter")),
        _entry("ash", "letter arrives", keys=("letter",)),
        _entry("ash", "one-off remark", keys=("garden",)),
    ]
    # rent and letter both appear twice; rent was seen first and stays ahead.
    assert key_vocab(ledger) == ["rent", "letter", "landlord", "garden"]
    assert key_vocab(ledger, n=2) == ["rent", "letter"]
    assert key_vocab(ledger, n=0) == []
    assert key_vocab([]) == []


def test_key_vocab_folds_case_and_ignores_dead_rows():
    from feltstate.memory.keyweb import key_vocab

    a = _entry("ash", "notes the Garden", keys=("Garden",))
    b = _entry("ash", "notes the garden again", keys=("garden", "gate"))
    dead = _entry("ash", "retracted belief", keys=("gate", "gate-key"))
    dead["_retracted"] = True

    vocab = key_vocab([a, b, dead])
    # counting is case-folded, the first spelling is what gets reported;
    # the retracted row's keys never vote, so "gate" holds one use, not two.
    assert vocab == ["Garden", "gate"]


# --------------------------------------------------------------------------- #
# The query leg — key_hits / walk_edges / chain / Canon.reach                 #
# --------------------------------------------------------------------------- #
def test_key_hits_validates_words_and_ranks_by_collisions():
    from feltstate.memory.keyweb import key_hits

    a = _entry("ash", "the rent dispute", keys=["rent", "dispute"])
    b = _entry("ash", "rent paid", keys=["rent"])
    dead = _entry("ash", "old rent gripe", keys=["rent"])
    dead["_retracted"] = True
    ledger = [b, a, dead]

    hits = key_hits(["RENT", "dispute", "not a key"], ledger)
    objs = [h["entry"]["what"]["object"] for h in hits]
    assert objs == ["the rent dispute", "rent paid"]  # most-collided first, dead excluded
    assert hits[0]["hits"] == 2 and hits[0]["shared"] == ["rent", "dispute"]
    assert key_hits(["a phrase never collides"], ledger) == []


def test_walk_edges_follows_judged_kin_and_stops_at_dead_rows():
    from feltstate.memory.keyweb import walk_edges

    seed = _entry("ash", "dispute starts", keys=["dispute", "landlord"])
    kin = _entry("ash", "landlord relents", keys=["landlord"], days_ago=10)
    far = _entry("ash", "movers booked", keys=["boxes"], days_ago=5)
    dead = _entry("ash", "retracted rumour", keys=["landlord"], days_ago=20)
    dead["_retracted"] = True
    ledger = [seed, kin, far, dead]

    from feltstate.memory.keyweb import _entry_id

    ts = "2026-01-01T00:00:00+00:00"
    seed["relates"] = [
        {"to": _entry_id(kin), "why": "same landlord", "ts": ts},
        {"to": _entry_id(dead), "why": "was kin once", "ts": ts},
    ]
    kin["relates"] = [
        {"to": _entry_id(seed), "why": "same landlord", "ts": ts},
        {"to": _entry_id(far), "why": "the move it forced", "ts": ts},
    ]

    one_hop = walk_edges([seed], ledger, hops=1)
    assert [r["entry"]["what"]["object"] for r in one_hop] == ["landlord relents"]
    assert one_hop[0]["via"] == "same landlord" and one_hop[0]["hop"] == 1

    two_hops = walk_edges([seed], ledger, hops=2)
    assert [r["entry"]["what"]["object"] for r in two_hops] == [
        "landlord relents",
        "movers booked",
    ]
    assert two_hops[1]["hop"] == 2  # judged one pair at a time; hop 2 is dilution, opt-in


def test_chain_orders_by_event_time_with_unstamped_first():
    from feltstate.memory.keyweb import chain

    newest = _entry("ash", "resolved", days_ago=1)
    oldest = _entry("ash", "raised", days_ago=40)
    unstamped = {"who": {"actor": "ash"}, "what": {"object": "background"}, "intensity": 0.5}

    ordered = chain([newest, unstamped, oldest])
    assert [r["what"]["object"] for r in ordered] == ["background", "raised", "resolved"]


def test_canon_reach_walks_the_web_and_tail_is_the_present(tmp_path):
    from feltstate.memory.keyweb import SharedKeyJudge, _entry_id, digest_canon

    path = tmp_path / "canon.jsonl"
    raised = _entry("ash", "rent raised", intensity=0.8, days_ago=40, keys=["rent", "landlord"])
    dispute = _entry(
        "ash", "dispute with landlord", intensity=0.8, days_ago=30, keys=["dispute", "landlord"]
    )
    resolved = _entry("ash", "rent resolved at last", intensity=0.7, days_ago=1, keys=["rent"])
    noise = _entry("ash", "bought a kettle", intensity=0.6, days_ago=15, keys=["kettle"])
    path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in (raised, dispute, resolved, noise))
        + "\n",
        encoding="utf-8",
    )
    c = Canon(path)
    # Judge binds raised <-> dispute (shared landlord... needs 2 shared keys, so use 1).
    digest_canon(c, [_entry_id(dispute)], judge=SharedKeyJudge(min_shared=1))

    report = c.reach("rent")
    objs = [f["object"] for f in report["chain"]]
    # Entered by key: raised + resolved. Gathered by edge: dispute. Noise stays out.
    assert objs == ["rent raised", "dispute with landlord", "rent resolved at last"]
    assert report["current"]["object"] == "rent resolved at last"  # the tail is the present
    assert report["hits"] == 2 and report["kin"] == 1

    by_obj = {f["object"]: f for f in report["chain"]}
    assert by_obj["rent raised"]["entered"] == "key" and "rent" in by_obj["rent raised"]["shared"]
    assert by_obj["dispute with landlord"]["entered"] == "edge"
    assert by_obj["dispute with landlord"]["via"]  # carries the judge's why
    assert all(f["recalls"] >= 1 for f in report["chain"])  # used memory sticks

    # limit drops the head, never the tail.
    short = c.reach("rent", limit=2)
    assert [f["object"] for f in short["chain"]] == [
        "dispute with landlord",
        "rent resolved at last",
    ]
    assert short["current"]["object"] == "rent resolved at last"
