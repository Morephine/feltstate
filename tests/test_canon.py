"""Tests for feltstate.memory.canon — the decaying 5W1H fact store.

Canon stores facts, decays their felt salience over time, dedups by
``(actor|object)``, and tiers entries into visible / archived / forgotten. The
behaviours pinned here:

* ``add`` then ``search`` finds the fact and bumps its ``recalls`` ("used memory
  sticks");
* a fact written at full default intensity fades into archive / forgotten after
  enough simulated days;
* a fact above ``permanent_above`` never decays;
* ``correct`` supersedes (old kept, hidden) and ``retract`` marks (kept, hidden).

Decay is age-driven, so to simulate the passage of days without sleeping we age
an entry by rewriting its on-disk ``ts`` field. That is the only "time travel"
trick used.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from feltstate import Canon


def _age_entries_on_disk(path, days: float) -> None:
    """Rewrite every record in a jsonl file so its ``ts`` is ``days`` in the
    past, simulating elapsed time for the decay calculation."""
    old_ts = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    out = []
    for ln in lines:
        rec = json.loads(ln)
        rec["ts"] = old_ts
        out.append(json.dumps(rec, ensure_ascii=False))
    path.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")


def _canon(tmp_path) -> Canon:
    return Canon(tmp_path / "canon.jsonl")


# --------------------------------------------------------------------------- #
# add / search / recall                                                       #
# --------------------------------------------------------------------------- #
def test_add_then_search_finds_and_increments_recall(tmp_path):
    c = _canon(tmp_path)
    c.add("user", "the prototype", action="shipped", why="months of work paid off")

    hits = c.search("prototype")
    assert len(hits) == 1
    hit = hits[0]
    assert hit["actor"] == "user"
    assert hit["object"] == "the prototype"
    assert hit["why"] == "months of work paid off"
    assert hit["recalls"] == 1  # the search itself counted as a recall

    # Searching again bumps recalls further (used memory sticks).
    again = c.search("prototype")
    assert again[0]["recalls"] == 2


def test_recall_boost_slows_decay(tmp_path):
    """A fact that is recalled repeatedly should sit at a higher salience than an
    identical un-recalled fact of the same age."""
    c = _canon(tmp_path)
    c.add("user", "fact alpha", why="reason a", intensity=0.5)
    c2_path = tmp_path / "other.jsonl"
    c2 = Canon(c2_path)
    c2.add("user", "fact beta", why="reason b", intensity=0.5)

    # Recall alpha several times to accrue recall boost.
    for _ in range(5):
        c.search("alpha")

    # Age both stores by the same amount, but keep them in the visible tier so
    # both still surface (0.5 - 10/90 = 0.39 > visible_threshold).
    _age_entries_on_disk(c.path, days=10)
    _age_entries_on_disk(c2.path, days=10)

    alpha = c.view()[0]
    beta = c2.view()[0]
    # Same base intensity, same age, but alpha was recalled -> higher salience.
    assert alpha["intensity"] > beta["intensity"]


def test_forgotten_facts_are_not_returned_or_boosted(tmp_path):
    c = _canon(tmp_path)
    c.add("user", "trivial thing", intensity=0.5)
    _age_entries_on_disk(c.path, days=120)  # 0.5 - 120/90 < 0 -> forgotten
    assert c.search("trivial") == []
    assert c.view() == []
    assert c.view(include_archived=True) == []


# --------------------------------------------------------------------------- #
# Decay into archive / forgotten over simulated days                          #
# --------------------------------------------------------------------------- #
def test_default_fact_decays_into_archive_then_forgotten(tmp_path):
    c = _canon(tmp_path)
    c.add("user", "an ordinary moment", intensity=0.5)

    # Fresh: visible.
    fresh = c.view()
    assert len(fresh) == 1 and fresh[0]["tier"] == "visible"

    # ~22 days: 0.5 - 22/90 = 0.256 -> below visible (0.30), above archive (0.10).
    _age_entries_on_disk(c.path, days=22)
    assert c.view() == []  # not visible anymore
    archived = c.view(include_archived=True)
    assert len(archived) == 1 and archived[0]["tier"] == "archived"

    # ~60 days: 0.5 - 60/90 < 0.10 -> forgotten, gone from every view.
    _age_entries_on_disk(c.path, days=60)
    assert c.view(include_archived=True) == []


def test_permanent_fact_never_decays(tmp_path):
    c = _canon(tmp_path)
    c.add("user", "the day everything changed", intensity=0.95, why="it mattered")
    _age_entries_on_disk(c.path, days=5000)  # ~13.7 years
    v = c.view()
    assert len(v) == 1
    entry = v[0]
    assert entry["permanent"] is True
    assert entry["tier"] == "visible"
    # Salience held at base, not decayed.
    assert entry["intensity"] >= 0.95 - 1e-6


def test_reinforce_on_duplicate_add_bumps_and_dedups(tmp_path):
    c = _canon(tmp_path)
    c.add("user", "the same fact", why="first")
    c.add("user", "the same fact", why="ignored on dup")  # same (actor|object)
    v = c.view()
    # Deduped to a single entry...
    assert len(v) == 1
    # ...whose reinforce count went up.
    assert v[0]["reinforced"] >= 1


# --------------------------------------------------------------------------- #
# Grey zone: ask -> confirm                                                   #
# --------------------------------------------------------------------------- #
def test_ask_is_grey_zone_and_confirm_promotes(tmp_path):
    c = _canon(tmp_path)
    pending = c.ask("user", "maybe likes tea", why="unsure")
    # Grey-zone facts start at the lower pending intensity, not the default.
    assert pending["base_intensity"] <= c.cfg.pending_intensity + 1e-6
    # Not yet in the confirmed view.
    assert c.view() == []

    promoted = c.confirm("tea")
    assert len(promoted) == 1
    assert promoted[0]["object"] == "maybe likes tea"
    # Now it lives in the confirmed store.
    assert any(e["object"] == "maybe likes tea" for e in c.view())


# --------------------------------------------------------------------------- #
# correct / retract                                                           #
# --------------------------------------------------------------------------- #
def test_correct_supersedes_old_keeps_new_visible(tmp_path):
    c = _canon(tmp_path)
    c.add("user", "lives in the old city", why="they said so")
    new = c.correct("old city", object="lives by the coast", why="they moved")
    assert new["object"] == "lives by the coast"

    objs = [e["object"] for e in c.view()]
    # The superseded fact is hidden; only the corrected one shows.
    assert "lives by the coast" in objs
    assert "lives in the old city" not in objs


def test_retract_hides_fact_but_keeps_record(tmp_path):
    c = _canon(tmp_path)
    c.add("user", "said something regrettable", why="heat of the moment")
    retracted = c.retract("regrettable")
    assert retracted  # non-empty rendered dict returned
    # Hidden from all views.
    assert c.view() == []
    assert c.search("regrettable") == []

    # But the record physically remains on disk (auditable), just marked.
    raw = c.path.read_text(encoding="utf-8")
    assert "regrettable" in raw
    assert "_retracted" in raw


def test_correct_and_retract_on_missing_target_return_empty(tmp_path):
    c = _canon(tmp_path)
    assert c.correct("nothing here", object="whatever") == {}
    assert c.retract("nothing here") == {}


# --------------------------------------------------------------------------- #
# compact                                                                     #
# --------------------------------------------------------------------------- #
def test_compact_moves_archived_drops_forgotten_keeps_visible(tmp_path):
    c = _canon(tmp_path)
    c.add("user", "vivid memory", intensity=0.95)  # permanent -> visible
    c.add("user", "fading memory", intensity=0.5)  # will be archived
    c.add("user", "lost memory", intensity=0.5)  # will be forgotten

    # Age only the two non-permanent ones by editing their ts selectively.
    lines = [json.loads(ln) for ln in c.path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    for rec in lines:
        obj = rec.get("what", {}).get("object", "")
        if obj == "fading memory":
            rec["ts"] = (datetime.now(timezone.utc) - timedelta(days=22)).isoformat()
        elif obj == "lost memory":
            rec["ts"] = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    c.path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in lines) + "\n", encoding="utf-8"
    )

    c.compact()

    # Visible (permanent) fact stays in the main store.
    remaining = [e["object"] for e in c.view()]
    assert "vivid memory" in remaining
    assert "fading memory" not in remaining  # moved to archive
    assert "lost memory" not in remaining  # dropped

    # The archived sibling file now holds the faded fact.
    archived_raw = c.archived_path.read_text(encoding="utf-8")
    assert "fading memory" in archived_raw
    assert "lost memory" not in archived_raw  # forgotten -> truly dropped

    # Compaction is idempotent.
    c.compact()
    assert "vivid memory" in [e["object"] for e in c.view()]
