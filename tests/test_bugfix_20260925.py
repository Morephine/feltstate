"""Regression pins for the 2026-09-25 fix batch.

Every test here pins a behaviour that was silently wrong before the fix and
names the failure it guards against: a store read that failed on one byte and
was written back empty (canon), rows split at characters that JSON writes raw
(canon, topics, reaper, chain), a tamper ledger that stopped verifying after
its second retention prune (chain), and a read-only viewer whose search wrote
to the store (dashboard).
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import feltstate.memory.lifecycle.chain as chain_mod
from feltstate.companion.topics import JsonlTopicsStore
from feltstate.dashboard import reach_payload
from feltstate.memory.canon import Canon
from feltstate.memory.keyweb import imprint_into
from feltstate.memory.lifecycle import Chain, execute
from feltstate.memory.skill import add_skill, recall_skills


def _objects(canon: Canon) -> set[str]:
    return {r["object"] for r in canon.view()}


def _one_row_store(tmp_path: Path) -> tuple[Path, Path]:
    store, ledger = tmp_path / "s.jsonl", tmp_path / "l.jsonl"
    store.write_text(json.dumps({"cid": "a", "text": "x", "fp": {"fp_id": "F"}}) + "\n")
    return store, ledger


@pytest.fixture
def clock(monkeypatch):
    """Drive the chain's wall clock by hand (patrol stamps, retention cutoff)."""
    state = {"now": datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc)}

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return state["now"]

    monkeypatch.setattr(chain_mod, "datetime", _Clock)
    return state


# --------------------------------------------------------------------------- #
# canon: one bad byte costs one row, never the store                          #
# --------------------------------------------------------------------------- #
def test_torn_multibyte_tail_costs_one_row_not_the_store(tmp_path):
    """Before: a crash that cut the last row inside a multi-byte character made
    read_text() fail for the *whole* file; every read saw an empty store and the
    next compact() wrote that emptiness back — nothing quarantined, no trace."""
    canon = Canon(tmp_path / "canon.jsonl")
    canon.add("阿树", "房租冻结正式生效")
    canon.add("Kay", "帮忙修好了花园篱笆")
    row = {"who": {"actor": "阿树"}, "what": {"object": "今天很开心"}}
    whole = json.dumps(row, ensure_ascii=False).encode("utf-8")
    torn = whole[: whole.index("开".encode()) + 1]  # cut inside a 3-byte character
    with open(canon.path, "ab") as f:
        f.write(torn)

    assert _objects(canon) == {"房租冻结正式生效", "帮忙修好了花园篱笆"}
    canon.compact()
    assert _objects(canon) == {"房租冻结正式生效", "帮忙修好了花园篱笆"}
    quarantine = canon.path.with_name("canon.jsonl.corrupt").read_text(encoding="utf-8")
    records = [json.loads(line) for line in quarantine.splitlines()]
    assert [r["reason"] for r in records] == ["invalid-utf8"]
    assert base64.b64decode(records[0]["raw_b64"]) == torn  # the exact bytes, kept


def test_an_append_after_a_torn_tail_starts_a_fresh_line(tmp_path):
    """Before: the next append was glued onto the torn row, so a good new fact
    was quarantined together with the bad one."""
    canon = Canon(tmp_path / "canon.jsonl")
    canon.add("ava", "likes tea")
    with open(canon.path, "a", encoding="utf-8") as f:
        f.write('{"who": {"actor": "ava"}, "wh')  # torn, no line feed
    canon.add("ava", "moved to Lisbon")
    assert _objects(canon) == {"likes tea", "moved to Lisbon"}


def test_confirm_with_a_torn_row_keeps_every_fact(tmp_path):
    """Before: confirm() rewrote the main store from what it had read — with one
    torn multi-byte row that was [], so promoting one fact (the path skill
    auto-promotion takes) deleted every other."""
    canon = Canon(tmp_path / "canon.jsonl")
    canon.add("阿树", "喜欢喝绿茶")
    canon.ask("阿树", "可能要搬家")
    with open(canon.path, "ab") as f:
        f.write("半".encode()[:2])
    canon.confirm("搬家")
    assert _objects(canon) == {"喜欢喝绿茶", "可能要搬家"}


def test_a_store_that_cannot_be_read_is_never_rewritten_as_empty(tmp_path, monkeypatch):
    """Before: an OSError on read came back as [] to every caller, and
    compact() / confirm() rewrote the file from it. Mutators now raise and leave
    the bytes alone; read paths keep their lenient contract (warn, show nothing)."""
    canon = Canon(tmp_path / "canon.jsonl")
    canon.add("ava", "likes tea")
    canon.ask("ava", "might move")
    before = canon.path.read_bytes()
    real = Path.read_bytes

    def unreadable_main(self):
        if self == canon.path:
            raise OSError("simulated read failure")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", unreadable_main)
    assert canon.view() == []
    with pytest.raises(OSError):
        canon.compact()
    with pytest.raises(OSError):
        canon.confirm("might move")
    monkeypatch.undo()

    assert canon.path.read_bytes() == before
    assert _objects(canon) == {"likes tea"}


def test_skill_recall_bump_never_rewrites_a_failed_reread(tmp_path, monkeypatch):
    """Before: recall_skills re-read the store for its recall bump and rewrote the
    result unconditionally, so one failed re-read replaced it with nothing."""
    canon = Canon(tmp_path / "canon.jsonl")
    add_skill(canon, "self", "summarise a long thread", why="catch-up notes")
    before = canon.pending_path.read_bytes()
    real = Path.read_bytes
    reads = {"n": 0}

    def second_read_fails(self):
        if self == canon.pending_path:
            reads["n"] += 1
            if reads["n"] >= 2:
                raise OSError("simulated transient read failure")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", second_read_fails)
    assert recall_skills(canon, "summarise")  # the selection itself still answers
    monkeypatch.undo()
    assert canon.pending_path.read_bytes() == before


# --------------------------------------------------------------------------- #
# JSONL readers: rows are split on line feeds only                            #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("sep", [" ", " ", "\x85"])
def test_line_separator_characters_round_trip_through_canon(tmp_path, sep):
    """Before: json.dumps(ensure_ascii=False) writes U+2028 / U+2029 / U+0085 raw
    and str.splitlines() breaks on them, so add() 'succeeded' but the fact read
    back as two corrupt halves — quarantined, then dropped by the next rewrite."""
    canon = Canon(tmp_path / "canon.jsonl")
    obj = f"copied from a web page{sep}second line"
    canon.add("ava", obj)
    canon.compact()
    assert [r["object"] for r in canon.view()] == [obj]
    assert not canon.path.with_name("canon.jsonl.corrupt").exists()


def test_topic_with_a_line_separator_survives_the_queue(tmp_path):
    """Before: the torn halves were skipped as bad lines — the topic was never
    raised, and the next mark_consumed() rewrite erased it."""
    store = JsonlTopicsStore(tmp_path / "topics.jsonl")
    store.append("ask about the deploy and the rent")
    store.append("the fireworks")
    assert store.read_oldest_unconsumed() == "ask about the deploy and the rent"
    store.mark_consumed("ask about the deploy and the rent")
    assert store.read_oldest_unconsumed() == "the fireworks"


def test_reaper_reads_rows_carrying_line_separators(tmp_path):
    """Before: json.loads raised on the first half of a split row, so a store
    holding one such row crashed the whole deletion cascade."""
    store = tmp_path / "s.jsonl"
    rows = [
        {"cid": "keep", "text": "line one line two", "fp": {"mid": "KEEP"}},
        {"cid": "dead", "text": "goes", "fp": {"mid": "DEAD"}},
    ]
    store.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )
    execute(
        {"dead_ids": ["DEAD"], "dead_sources": [], "prune": {}},
        stores=[store],
        ledger_path=tmp_path / "l.jsonl",
        pending_path=tmp_path / "p.json",
        txid="t",
        now_iso="2026-09-25T00:00:00+00:00",
    )
    left = [json.loads(x) for x in store.read_text(encoding="utf-8").split("\n") if x]
    assert [r["cid"] for r in left] == ["keep"]
    assert left[0]["text"] == "line one line two"


def test_chain_snapshot_keeps_a_row_with_a_line_separator_whole(tmp_path):
    """Before: the watchdog saw two raw fragments where the store held one row."""
    store, ledger = tmp_path / "s.jsonl", tmp_path / "l.jsonl"
    row = {"cid": "a", "text": "one two", "fp": {"fp_id": "F"}}
    store.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    added = Chain(ledger, [store]).patrol()["added"]
    assert [k.split("::")[-1] for k in added] == ["a"]


# --------------------------------------------------------------------------- #
# chain: one epoch, at the head — across any number of retention prunes       #
# --------------------------------------------------------------------------- #
def test_chain_verifies_through_every_daily_prune(tmp_path, clock):
    """Before: each prune kept the previous epoch — stamped at prune time, so
    younger than the links it anchored — and stacked a new one above it. From
    the second prune on, verify_full() failed an honest ledger for good (day 63
    of a daily patrol with the default 60-day window)."""
    store, ledger = _one_row_store(tmp_path)
    ch = Chain(ledger, [store], keep_days=60)
    for day in range(1, 76):
        clock["now"] += timedelta(days=1)
        ch.patrol()
        assert ch.verify_full(), f"day {day}"
    lines = [json.loads(x) for x in ledger.read_text().splitlines()]
    assert [i for i, j in enumerate(lines) if j.get("epoch")] == [0]  # one, at the head


def test_prune_heals_a_ledger_that_already_stacked_epochs(tmp_path, clock):
    """A ledger written by the old prune carries stale epochs under the live one;
    the next prune keeps only the epoch naming the first surviving link."""
    store, ledger = _one_row_store(tmp_path)
    ch = Chain(ledger, [store], keep_days=60)
    for _ in range(63):  # the first prune (day 62) writes the head epoch
        clock["now"] += timedelta(days=1)
        ch.patrol()
    lines = ledger.read_text().splitlines()
    assert json.loads(lines[0]).get("epoch")
    stale = {"epoch": True, "fp": "0" * 24, "prev": "epoch:" + "0" * 24, "ts": "x"}
    ledger.write_text("\n".join([lines[0], json.dumps(stale), *lines[1:]]) + "\n")
    assert ch.verify_full() is False  # the layout the old prune left behind

    ch.patrol()  # same instant: nothing new ages out, the stack is still cleared
    assert ch.verify_full()
    epochs = [x for x in ledger.read_text().splitlines() if json.loads(x).get("epoch")]
    assert epochs == [lines[0]]


def test_verify_rejects_an_epoch_inside_the_chain(tmp_path):
    """Before: an epoch was trusted wherever it stood, so links cut out of the
    middle of the ledger — the patrols that raised an alarm, say — could be
    papered over with one hand-written epoch and verify_full() still passed."""
    store, ledger = _one_row_store(tmp_path)
    ch = Chain(ledger, [store])
    for _ in range(5):
        ch.patrol()
    links = [json.loads(x) for x in ledger.read_text().splitlines()]
    forged = {"epoch": True, "fp": links[3]["prev"], "prev": "epoch:x", "ts": "x"}
    ledger.write_text("\n".join(json.dumps(x) for x in [links[0], forged, *links[3:]]) + "\n")
    assert ch.verify_full() is False


def test_unreadable_ledger_stamps_do_not_stop_the_patrol(tmp_path):
    """Before: retention parsed every stamp with fromisoformat, so one tombstone
    written with an unreadable ts (or any 'Z' stamp on Python 3.10) made every
    later patrol raise after appending its link."""
    store, ledger = _one_row_store(tmp_path)
    ch = Chain(ledger, [store])
    ch.patrol()
    zulu = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with ledger.open("a", encoding="utf-8") as f:
        for txid, ts in (("t1", zulu), ("t2", "yesterday")):
            f.write(json.dumps({"event": "legal_death", "txid": txid, "cids": [], "ts": ts}) + "\n")
    ch.patrol()
    assert ch.verify_full()
    txids = [json.loads(x).get("txid") for x in ledger.read_text().splitlines()]
    assert "t1" in txids and "t2" in txids  # unreadable is not a reason to drop


def test_a_torn_ledger_write_does_not_swallow_the_next_link(tmp_path):
    """Before: half a line cut inside a multi-byte character made every read of
    the ledger raise, and the next link would have been glued onto the tear."""
    store, ledger = _one_row_store(tmp_path)
    ch = Chain(ledger, [store])
    ch.patrol()
    first = ch.last_link()
    with ledger.open("ab") as f:
        f.write('{"fp": "torn", "payload": {"note": "半'.encode()[:-1])
    ch.patrol()
    last = ch.last_link()
    assert first is not None and last is not None
    assert last["prev"] == first["fp"]  # a whole link, on its own line
    assert ch.verify_full() is False  # the tear itself is still damage, and says so


def test_a_non_object_ledger_line_fails_verify_instead_of_raising(tmp_path):
    store, ledger = _one_row_store(tmp_path)
    ch = Chain(ledger, [store])
    ch.patrol()
    with ledger.open("a", encoding="utf-8") as f:
        f.write("[]\n")
    assert ch.verify_full() is False


# --------------------------------------------------------------------------- #
# dashboard: searching is looking, not recalling                              #
# --------------------------------------------------------------------------- #
def test_dashboard_search_leaves_the_store_byte_identical(tmp_path):
    """Before: /api/reach called Canon.reach(), which bumps recalls and rewrites
    the store — a 'zero writes' viewer whose every search made the facts it
    found decay more slowly."""
    canon = Canon(tmp_path / "canon.jsonl")
    fact = canon.add("ash", "the rent freeze became official")
    imprint_into(canon, fact["id"], ["rent"])
    before = canon.path.read_bytes()
    for _ in range(3):
        got = reach_payload(canon, "rent")
    assert [c["id"] for c in got["chain"]] == [fact["id"]]
    assert canon.path.read_bytes() == before
    assert reach_payload(canon, "  ") == {"chain": []}


def test_agent_reach_still_bumps_and_walks_the_same_chain(tmp_path):
    """The agent's own tool keeps its contract: the same chain, and used memory
    sticks."""
    canon = Canon(tmp_path / "canon.jsonl")
    fact = canon.add("ash", "the rent freeze became official")
    imprint_into(canon, fact["id"], ["rent"])
    looked = canon.reach("rent", bump=False)
    used = canon.reach("rent")
    assert [c["id"] for c in looked["chain"]] == [c["id"] for c in used["chain"]]
    assert looked["chain"][0]["recalls"] == 0
    assert used["chain"][0]["recalls"] == 1
