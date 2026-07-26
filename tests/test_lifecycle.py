"""Lifecycle — birth fingerprints (unique instance id vs content hash), tiered
decay clocks with validated inputs, authority-aware GC keyed by instance id, a
transactional power-cut-replayable reaper that purges snapshots, and a chain
witness that seals full state and tells a lawful death from an evaporation.
Pure-program, fully deterministic."""

import json

import pytest

from feltstate.memory.lifecycle import (
    Chain,
    ClockConfig,
    FingerprintError,
    GCError,
    ReaperError,
    core_hash,
    current_intensity,
    execute,
    fuse,
    is_collectable,
    make_fingerprint,
    make_source_ptr,
    prune_lineage,
    replay_if_pending,
    resolve_deaths,
    verify_fingerprint,
    verify_source_ptr,
)

AFFECT = {"valence": 0.2, "arousal": 0.5}
TS = "2026-01-02T03:04:05+00:00"


def _fp(mid, seed, src=(), lineage=()):
    ptr = make_source_ptr("raw/2026-01-02.md", "03:00", "03:05", seed)
    return make_fingerprint([ptr], AFFECT, TS, mid=mid, src=list(src), lineage=list(lineage))


# --- fingerprint ----------------------------------------------------------- #
def test_seals_core_and_catches_tampering():
    fp = _fp("m1", "she said the tide was loud")
    assert verify_fingerprint(fp)
    forged = json.loads(json.dumps(fp))
    forged["core"]["birth_affect"]["valence"] = 0.99
    assert not verify_fingerprint(forged)


def test_verify_fails_closed_on_garbage():
    for junk in (None, {}, {"core": {}}, {"fp_id": "x"}, 42, {"core": 1, "fp_id": "y"}):
        assert verify_fingerprint(junk) is False  # never raises


def test_verify_rejects_schema_invalid_core_even_with_matching_hash():
    fp = _fp("m1", "source")
    fp["core"]["birth_affect"] = {"valence": "not-a-number"}
    fp["fp_id"] = core_hash(fp["core"]["source_ptrs"], fp["core"]["birth_affect"], fp["core"]["ts"])
    assert verify_fingerprint(fp) is False


def test_source_pointer_verifies_exact_text_only():
    ptr = make_source_ptr("chat.json", "10:00", "10:01", "exact source text")
    assert len(ptr["sha"]) == 64
    assert verify_source_ptr(ptr, "exact source text") is True
    assert verify_source_ptr(ptr, "Exact source text") is False

    legacy = dict(ptr)
    legacy["sha"] = legacy["sha"][:16]
    assert verify_source_ptr(legacy, "exact source text") is True


def test_unique_id_is_separate_from_content_hash():
    a = _fp("row-A", "identical")
    b = _fp("row-B", "identical")
    assert a["fp_id"] == b["fp_id"]  # same content -> same checksum (legit)
    assert a["mid"] != b["mid"]  # different memory -> different identity


def test_genealogy_lives_outside_the_seal():
    fp = _fp("m1", "a small fact", src=["parentA"])
    fp["src"] = ["somebody-else"]
    fp["lineage"] = ["adopted"]
    assert verify_fingerprint(fp)


def test_constructors_validate_inputs():
    ptr = make_source_ptr("f", "a", "b", "text")
    with pytest.raises(FingerprintError):
        make_fingerprint([ptr], AFFECT, TS, mid="")  # empty id
    with pytest.raises(FingerprintError):
        make_fingerprint([], AFFECT, TS, mid="m")  # empty provenance
    with pytest.raises(FingerprintError):
        make_fingerprint([ptr], {"v": float("nan")}, TS, mid="m")  # NaN affect
    with pytest.raises(FingerprintError):
        make_fingerprint([ptr], AFFECT, "2026-01-02T03:04:05", mid="m")  # naive ts
    with pytest.raises(FingerprintError):
        make_source_ptr("", "a", "b", "text")
    with pytest.raises(FingerprintError):
        make_fingerprint(
            [{"file": "f", "t0": "a", "t1": "b", "sha": "not-a-sha"}],
            AFFECT,
            TS,
            mid="m",
        )


def test_fuse_and_prune_by_mid():
    a, b = _fp("kid-A", "first"), _fp("kid-B", "second")
    fused = fuse([a, b], AFFECT, TS, mid="fused-1")
    assert len(fused["core"]["source_ptrs"]) == 2
    assert set(fused["lineage"]) == {"kid-A", "kid-B"}
    before = fused["fp_id"]
    prune_lineage(fused, {"kid-B"})
    assert fused["fp_id"] == before and verify_fingerprint(fused)
    assert fused["lineage"] == ["kid-A"]
    assert len(fused["core"]["source_ptrs"]) == 2  # instinct memory stands


def test_fuse_rejects_unverified_children():
    bad = _fp("bad", "source")
    bad["core"]["birth_affect"]["valence"] = 0.99
    with pytest.raises(FingerprintError, match="unverifiable"):
        fuse([bad], AFFECT, TS, mid="laundered")


# --- clocks ---------------------------------------------------------------- #
def test_permanent_line_and_gear_ordering():
    cfg = ClockConfig()
    assert current_intensity(0.9, 10_000, "fact", cfg) == 0.9
    y = 365.0
    t = current_intensity(0.6, y, "trauma", cfg, valence=-0.5)
    w = current_intensity(0.6, y, "warmth", cfg, valence=0.5)
    f = current_intensity(0.6, y, "fact", cfg, valence=0.0)
    assert t > w > f


def test_no_floor_and_finite_validation():
    cfg = ClockConfig()
    assert current_intensity(0.3, 3650, "fact", cfg) < cfg.death_line
    for bad in (float("nan"), float("inf"), -0.1):
        with pytest.raises(ValueError):
            current_intensity(bad, 10, "fact", cfg)
    assert current_intensity(0.5, -100, "fact", cfg) == 0.5  # future ts -> age 0


def test_finite_validation_covers_age_and_valence():
    # The finite guard is not just for base: age_days and valence are equally
    # untrusted seams, and a non-finite value in either must raise rather than
    # silently produce a surprising intensity.
    cfg = ClockConfig()
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            current_intensity(0.5, bad, "fact", cfg)
        with pytest.raises(ValueError):
            current_intensity(0.5, 10, "fact", cfg, valence=bad)


def test_zero_or_negative_gear_is_rejected():
    # A gear of 0 would zero out the decay rate (accidental immortality); a
    # negative gear would flip decay into growth. Both must be refused at the door.
    with pytest.raises(ValueError):
        current_intensity(0.5, 10, "broken", ClockConfig(gears={"broken": 0.0}))
    with pytest.raises(ValueError):
        current_intensity(0.5, 10, "broken", ClockConfig(gears={"broken": -1.0}))


def test_negative_valence_lingers_longer_than_positive():
    # The asymmetry feltstate leans on everywhere: for the same kind and age, a
    # negative-valence memory decays slower than a positive one (bad moods
    # linger) -- which is what makes comfort *from someone* carry weight.
    cfg = ClockConfig()
    age = 200.0
    neg = current_intensity(0.6, age, "fact", cfg, valence=-0.8)
    pos = current_intensity(0.6, age, "fact", cfg, valence=0.8)
    assert neg > pos


# --- gc -------------------------------------------------------------------- #
def _store():
    protector = {"kind": "distilled", "_i": 0.5, "fp": _fp("PROT", "lesson", src=["FACT1"])}
    shielded = {"kind": "fact", "_i": 0.0, "fp": _fp("FACT1", "its source fact")}
    doomed = {"kind": "fact", "_i": 0.0, "fp": _fp("FACT2", "unloved fact")}
    legacy = {"kind": "fact", "_i": 0.0, "fp": {"backfill": "coarse", "mid": "OLD"}}
    fused = {"kind": "distilled", "_i": 0.6, "fp": _fp("FUSE", "fusion", lineage=["FACT2"])}
    return [protector, shielded, doomed, legacy, fused]


def test_gc_authority_mercy_and_pruning():
    plan = resolve_deaths(_store(), lambda m: m["_i"])
    assert "FACT1" not in plan["dead_ids"]  # shielded by living protector
    assert "FACT2" in plan["dead_ids"]  # unloved & faded
    assert plan["skipped_legacy"] == 1  # mercy rule
    assert plan["prune"] == {"FUSE": ["FACT2"]}


def test_gc_transitive_protection():
    # grandparent (alive) -> parent (faded) -> child (faded): all retained
    gp = {"kind": "distilled", "_i": 0.9, "fp": _fp("GP", "grandparent", src=["PARENT"])}
    parent = {"kind": "distilled", "_i": 0.0, "fp": _fp("PARENT", "parent", src=["CHILD"])}
    child = {"kind": "fact", "_i": 0.0, "fp": _fp("CHILD", "child")}
    plan = resolve_deaths([gp, parent, child], lambda m: m["_i"])
    assert plan["dead_ids"] == []  # authority flows all the way down


def test_gc_rejects_invalid_fingerprint_and_duplicate_id():
    tampered = {"kind": "fact", "_i": 0.0, "fp": _fp("X", "real")}
    tampered["fp"]["core"]["ts"] = "2020-01-01T00:00:00+00:00"  # break the seal
    plan = resolve_deaths([tampered], lambda m: m["_i"])
    assert plan["dead_ids"] == [] and plan["skipped_legacy"] == 1  # unverifiable -> exempt
    dup = [
        {"kind": "fact", "_i": 0.0, "fp": _fp("SAME", "a")},
        {"kind": "fact", "_i": 0.0, "fp": _fp("SAME", "b")},
    ]
    with pytest.raises(GCError):
        resolve_deaths(dup, lambda m: m["_i"])


def test_gc_honors_configured_death_line():
    faded = {"kind": "fact", "_i": 0.1, "fp": _fp("F", "faded")}
    assert resolve_deaths([faded], lambda m: m["_i"], death_line=0.05)["dead_ids"] == []
    assert resolve_deaths([faded], lambda m: m["_i"], death_line=0.2)["dead_ids"] == ["F"]


# --- reaper + chain -------------------------------------------------------- #
def test_reaper_cascade_snapshot_purge_and_replay(tmp_path):
    store, snap = tmp_path / "store.jsonl", tmp_path / "snap.jsonl"
    ledger, pending = tmp_path / "ledger.jsonl", tmp_path / "pending.json"
    rows = [
        {"cid": "keep-row", "text": "stays", "fp": {"mid": "KEEP", "lineage": ["DEAD"]}},
        {"cid": "dead-row", "text": "goes", "fp": {"mid": "DEAD"}},
    ]
    for p in (store, snap):
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    plan = {"dead_ids": ["DEAD"], "dead_sources": [], "prune": {"KEEP": ["DEAD"]}}
    execute(
        plan,
        stores=[store],
        ledger_path=ledger,
        pending_path=pending,
        snapshot_paths=[snap],
        txid="tx1",
        now_iso=TS,
    )

    assert [json.loads(x)["cid"] for x in store.read_text().splitlines()] == ["keep-row"]
    assert json.loads(store.read_text().splitlines()[0])["fp"]["lineage"] == []
    assert [json.loads(x)["cid"] for x in snap.read_text().splitlines()] == ["keep-row"]
    assert not pending.exists()
    ev = json.loads(ledger.read_text().splitlines()[0])
    assert ev["event"] == "legal_death" and ev["cids"] == ["dead-row"] and ev["txid"] == "tx1"

    # power-cut replay: same txid -> idempotent, one tombstone, identical end state
    pending.write_text(json.dumps({**plan, "txid": "tx1", "snaps": [str(snap)], "ts": TS}) + "\n")
    replay_if_pending(stores=[store], ledger_path=ledger, pending_path=pending)
    assert [json.loads(x)["cid"] for x in store.read_text().splitlines()] == ["keep-row"]
    assert (
        sum(1 for x in ledger.read_text().splitlines() if json.loads(x).get("txid") == "tx1") == 1
    )  # not duplicated


def test_reaper_fails_closed_on_malformed_pending(tmp_path):
    pending = tmp_path / "pending.json"
    pending.write_text(json.dumps({"no": "txid"}) + "\n")
    with pytest.raises(ReaperError):
        replay_if_pending(stores=[], ledger_path=tmp_path / "l.jsonl", pending_path=pending)
    assert pending.exists()  # not silently discarded


def test_chain_seals_state_against_tail_forgery(tmp_path):
    store, ledger = tmp_path / "s.jsonl", tmp_path / "l.jsonl"
    store.write_text(json.dumps({"cid": "a", "text": "original", "fp": {"fp_id": "FA"}}) + "\n")
    ch = Chain(ledger, [store])
    ch.patrol()
    # forge: rewrite the row AND the last link's stored state to match — must still fail
    store.write_text(json.dumps({"cid": "a", "text": "REWRITTEN", "fp": {"fp_id": "FA"}}) + "\n")
    from feltstate.memory.lifecycle.chain import default_bite

    lines = ledger.read_text().splitlines()
    tail = json.loads(lines[-1])
    key = list(tail["state"])[0]
    tail["state"][key] = default_bite({"text": "REWRITTEN", "fp": {"fp_id": "FA"}}, "")
    lines[-1] = json.dumps(tail, ensure_ascii=False)
    ledger.write_text("\n".join(lines) + "\n")
    assert ch.verify_full() is False  # sealed state -> forgery caught


def test_chain_lawful_death_vs_evaporation(tmp_path):
    store, ledger = tmp_path / "s.jsonl", tmp_path / "l.jsonl"
    alarms = []
    rows = [
        {"cid": "a", "text": "alpha", "fp": {"mid": "FA", "fp_id": "HA"}},
        {"cid": "b", "text": "beta", "fp": {"mid": "FB", "fp_id": "HB"}},
    ]
    store.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    ch = Chain(ledger, [store], on_alarm=alarms.append)
    ch.patrol()
    execute(
        {"dead_ids": ["FB"], "dead_sources": [], "prune": {}},
        stores=[store],
        ledger_path=ledger,
        pending_path=tmp_path / "p.json",
        txid="t",
        now_iso=TS,
    )
    r = ch.patrol()
    assert alarms == [] and [k.split("::")[-1] for k in r["lawful_deaths"]] == ["b"]
    store.write_text("")  # untombstoned evaporation
    ch.patrol()
    assert alarms and "evaporated" in alarms[0]
    assert ch.verify_full()  # links + tombstone events still verify


def test_chain_catches_mutation(tmp_path):
    store, ledger = tmp_path / "s.jsonl", tmp_path / "l.jsonl"
    alarms = []
    store.write_text(json.dumps({"cid": "a", "text": "orig", "fp": {"fp_id": "F"}}) + "\n")
    ch = Chain(ledger, [store], on_alarm=alarms.append)
    ch.patrol()
    store.write_text(json.dumps({"cid": "a", "text": "changed", "fp": {"fp_id": "F"}}) + "\n")
    ch.patrol()
    assert alarms and "mutated" in alarms[0]


def test_chain_verifies_across_pruning(tmp_path):
    store, ledger = tmp_path / "s.jsonl", tmp_path / "l.jsonl"
    store.write_text(json.dumps({"cid": "a", "text": "x", "fp": {"fp_id": "F"}}) + "\n")
    ch = Chain(ledger, [store], keep_days=60)
    for _ in range(4):
        ch.patrol()  # build a real 4-link chain
    # backdate the first two links past the retention window (they'll be dropped;
    # their now-stale fp doesn't matter — the point is head-pruning + re-anchor)
    lines = ledger.read_text().splitlines()
    for i in (0, 1):
        j = json.loads(lines[i])
        j["payload"]["ts"] = "2020-01-01T00:00:00+00:00"
        lines[i] = json.dumps(j, ensure_ascii=False)
    ledger.write_text("\n".join(lines) + "\n")
    ch.patrol()  # drops the two old links, re-anchors
    assert any('"epoch"' in ln for ln in ledger.read_text().splitlines())
    assert ch.verify_full()  # still verifiable across the prune


def test_collectable_definition():
    assert not is_collectable({"fp": None})
    assert not is_collectable({"fp": {"backfill": "coarse", "mid": "x"}})
    assert not is_collectable(
        {
            "fp": {
                "mid": "x",
                "fp_id": "wrong",
                "core": {"source_ptrs": [], "birth_affect": {}, "ts": ""},
            }
        }
    )
    assert is_collectable({"fp": _fp("good", "real")})


def test_an_unexplained_loss_keeps_alarming_until_acknowledged(tmp_path):
    """An evaporation must not go quiet on the next round.

    The following patrol simply took the current state as the new baseline, so
    it reported ``missing: []`` and the loss became invisible — a one-shot
    notification for something nobody had explained. It now carries in
    ``unresolved`` and keeps alarming until an operator passes
    ``rebaseline=True``.
    """
    store, ledger = tmp_path / "s.jsonl", tmp_path / "l.jsonl"
    alarms = []
    rows = [{"cid": "a", "text": "alpha", "fp": {"mid": "FA", "fp_id": "HA"}}]
    store.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    ch = Chain(ledger, [store], on_alarm=alarms.append)
    ch.patrol()

    store.write_text("")  # untombstoned evaporation
    first = ch.patrol()
    assert first["missing"] and len(alarms) == 1

    second = ch.patrol()
    assert second["missing"] == []  # nothing NEW went missing
    assert second["unresolved"], "the earlier loss stopped being reported"
    assert len(alarms) == 2, "the watchdog went quiet on an unexplained loss"

    cleared = ch.patrol(rebaseline=True)
    assert cleared["unresolved"] == []
    assert len(alarms) == 2  # an explicit acknowledgement is not an alarm


def test_a_malformed_ledger_line_does_not_disable_the_watchdog(tmp_path):
    """The tamper-evidence reader must not be crashable by tampering.

    ``json.loads`` happily returns a list, and every consumer called ``.get()``
    on whatever it got: one appended ``[]`` raised ``AttributeError`` out of
    ``verify_full()`` and ``patrol()``, permanently disabling the watchdog
    instead of alarming.
    """
    store, ledger = tmp_path / "s.jsonl", tmp_path / "l.jsonl"
    store.write_text(json.dumps({"cid": "a", "text": "alpha"}) + "\n")
    ch = Chain(ledger, [store], on_alarm=lambda m: None)
    ch.patrol()

    with ledger.open("a", encoding="utf-8") as fh:
        fh.write("[]\n")

    assert ch.verify_full() is False  # reported as broken, not raised
    ch.patrol()  # and the patrol still runs


def test_a_non_iso_tombstone_stamp_does_not_break_later_patrols(tmp_path):
    """``now_iso`` is caller-supplied free text; one odd stamp used to raise
    ``ValueError`` out of every subsequent patrol via the pruning pass."""
    store, ledger = tmp_path / "s.jsonl", tmp_path / "l.jsonl"
    store.write_text(json.dumps({"cid": "a", "text": "alpha"}) + "\n")
    ch = Chain(ledger, [store], on_alarm=lambda m: None)
    ch.patrol()
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps({"event": "legal_death", "cids": ["a"], "ts": "not-a-timestamp"}) + "\n"
        )

    ch.patrol()  # must not raise


def test_the_reaper_survives_a_torn_tombstone_line(tmp_path):
    """A crash-recovery module has to survive the crash it exists for.

    The tombstone is an append, so a crash mid-write leaves a partial final
    line. A bare ``json.loads`` over the ledger then raised ``JSONDecodeError``
    on every subsequent ``execute()`` and ``replay_if_pending()``: the pending
    ledger could never be cleared and the deletion could never complete.
    """
    store, ledger = tmp_path / "s.jsonl", tmp_path / "l.jsonl"
    rows = [{"cid": "a", "text": "alpha", "fp": {"mid": "FA", "fp_id": "HA"}}]
    store.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    ledger.write_text('{"event": "legal_death", "txid": "tx0", "ci')  # torn append

    execute(
        {"dead_ids": ["FA"], "dead_sources": [], "prune": {}},
        stores=[store],
        ledger_path=ledger,
        pending_path=tmp_path / "p.json",
        txid="t1",
        now_iso=TS,
    )

    remaining = [ln for ln in store.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert remaining == []  # the deletion actually completed
    assert not (tmp_path / "p.json").exists()  # and the pending ledger was cleared
