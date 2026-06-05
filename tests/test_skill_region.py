"""Tests for feltstate.memory.skill — the human-rated skill region inside canon.

A skill carries human 1/2/3 ratings (1 lousy / 2 ok / 3 excellent), given per task
and shared across the skills it used. Behaviours pinned here:

* utility = shrunk mean of ratings; a self-reported source moves nothing.
* promotion: three "3"s and no "1" → auto-promote to the confirmed store; a mixed
  record stays grey, flagged unstable; enough "1"s → retire.
* selection is probabilistic, weighted by rating (proven dominates, a low one is
  still explored) — so no skill monopolises and a newly-good one can rise.
* grey skills decay slowly (a long lease); skills never auto-surface, never go
  permanent, and the rating region never touches a fact.
* RatingGate rate-limits the ask (10-min cooldown + daily cap).
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import pytest

from feltstate import Engine, KeywordSource
from feltstate.config import MemoryConfig
from feltstate.memory.canon import Canon
from feltstate.memory.skill import (
    SKILL_REGION,
    RatingGate,
    SkillRatifier,
    _new_skill_meta,
    _utility,
    add_skill,
    ratify_skill,
    rating_priority,
    recall_skills,
    record_rating,
    record_task_rating,
    review_skills,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #
def _canon(tmp_path) -> Canon:
    return Canon(tmp_path / "canon.jsonl")


def _lines(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _skills_in(path) -> list[dict]:
    return [e for e in _lines(path) if e.get("region") == SKILL_REGION]


def _meta_of(canon, obj) -> dict:
    e = next(
        e
        for e in _skills_in(canon.path) + _skills_in(canon.pending_path)
        if e["what"]["object"] == obj
    )
    return e["skill"]


def _age_entries_on_disk(path, days: float) -> None:
    old_ts = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    out = []
    for ln in lines:
        rec = json.loads(ln)
        rec["ts"] = old_ts
        out.append(json.dumps(rec, ensure_ascii=False))
    path.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")


class _Yes(SkillRatifier):
    def ratify(self, candidate) -> bool:
        return True


class _No(SkillRatifier):
    def ratify(self, candidate) -> bool:
        return False


# --------------------------------------------------------------------------- #
# Utility from ratings                                                        #
# --------------------------------------------------------------------------- #
def test_utility_is_a_shrunk_mean_of_ratings():
    cfg = MemoryConfig()
    assert _utility(_new_skill_meta(cfg), cfg) == cfg.skill_seed  # unrated -> prior seed
    hi = _utility({"n1": 0, "n2": 0, "n3": 5}, cfg)
    lo = _utility({"n1": 5, "n2": 0, "n3": 0}, cfg)
    mid = _utility({"n1": 0, "n2": 5, "n3": 0}, cfg)
    assert hi > 0.75 and lo < 0.15 and 0.35 < mid < 0.55  # excellent high, lousy low, ok middling
    # the prior gives inertia: one rating can't swing it to an extreme
    assert _utility({"n1": 0, "n2": 0, "n3": 1}, cfg) < 0.7


# --------------------------------------------------------------------------- #
# record_rating: counts, ground-truth gate, create-on-miss                    #
# --------------------------------------------------------------------------- #
def test_rating_updates_counts_and_utility(tmp_path):
    c = _canon(tmp_path)
    s = add_skill(c, "self", "rebuild the index", grey=False)
    u0 = s["utility"]
    r = record_rating(c, s["id"], 3, source="human")
    assert r["n3"] == 1 and r["utility"] > u0  # an excellent rating lifts utility


def test_self_reported_source_is_a_noop(tmp_path):
    c = _canon(tmp_path)
    s = add_skill(c, "self", "deploy", grey=False)
    before = c.path.read_bytes()
    assert record_rating(c, s["id"], 1, source="reply_model") == {}  # the model can't rate itself
    assert record_rating(c, s["id"], 3, source="self_report") == {}
    assert c.path.read_bytes() == before  # nothing written


def test_rating_an_unknown_skill_births_a_grey_candidate(tmp_path):
    c = _canon(tmp_path)
    r = record_rating(c, "handle the weird CSV", 1, source="human", note="threw on quotes")
    assert r["proven"] is False and r["n1"] == 1
    assert _skills_in(c.pending_path) and _skills_in(c.path) == []


def test_rating_rejects_out_of_range(tmp_path):
    c = _canon(tmp_path)
    s = add_skill(c, "self", "x", grey=False)
    with pytest.raises(ValueError):
        record_rating(c, s["id"], 4, source="human")


# --------------------------------------------------------------------------- #
# Promotion / retirement by human ratings                                     #
# --------------------------------------------------------------------------- #
def test_three_excellent_ratings_auto_promote_to_main(tmp_path):
    c = _canon(tmp_path)
    cand = add_skill(c, "self", "tricky merge", grey=True)
    assert _skills_in(c.path) == []  # starts grey
    r1 = record_rating(c, cand["id"], 3, source="human")
    r2 = record_rating(c, cand["id"], 3, source="human")
    assert r1["proven"] is False and r2["proven"] is False  # not yet
    r3 = record_rating(c, cand["id"], 3, source="human")  # the third "3"
    assert r3["proven"] is True  # auto-promoted
    assert any(e["what"]["object"] == "tricky merge" for e in _skills_in(c.path))
    assert [e for e in _skills_in(c.pending_path) if c._is_active(e)] == []


def test_a_single_lousy_rating_blocks_auto_promotion(tmp_path):
    c = _canon(tmp_path)
    cand = add_skill(c, "self", "shaky trick", grey=True)
    record_rating(c, cand["id"], 3, source="human")
    record_rating(c, cand["id"], 1, source="human")  # one lousy -> unstable
    record_rating(c, cand["id"], 3, source="human")
    r = record_rating(c, cand["id"], 3, source="human")  # 3 threes, but a one exists
    assert r["proven"] is False and r["unstable"] is True  # held grey, flagged unstable
    assert _skills_in(c.path) == []  # NOT auto-promoted


def test_enough_lousy_ratings_retire_the_skill(tmp_path):
    c = _canon(tmp_path)
    s = add_skill(c, "self", "bad approach", grey=False)
    for _ in range(3):  # skill_retire_bad_count == 3
        record_rating(c, s["id"], 1, source="human")
    assert _meta_of(c, "bad approach")["retired"] is True
    assert recall_skills(c, "bad approach") == []  # retired -> never returned


def test_unstable_skill_can_still_be_ratified_by_introspection(tmp_path):
    c = _canon(tmp_path)
    cand = add_skill(c, "self", "context dependent", grey=True)
    for r in (3, 1, 3, 3):
        record_rating(c, cand["id"], r, source="human")  # unstable, auto-promo blocked
    assert ratify_skill(c, cand["id"], judge=_Yes()) is True  # introspection decides to keep it
    assert any(e["what"]["object"] == "context dependent" for e in _skills_in(c.path))


# --------------------------------------------------------------------------- #
# Task-level rating (credit shared across the skills a task used)             #
# --------------------------------------------------------------------------- #
def test_task_rating_is_shared_across_the_skills_used(tmp_path):
    c = _canon(tmp_path)
    a = add_skill(c, "self", "skill a", grey=False)
    b = add_skill(c, "self", "skill b", grey=False)
    out = record_task_rating(c, [a["id"], b["id"]], 3, source="human")
    assert len(out) == 2
    assert _meta_of(c, "skill a")["n3"] == 1 and _meta_of(c, "skill b")["n3"] == 1


# --------------------------------------------------------------------------- #
# Probabilistic, rating-weighted selection (explore/exploit)                  #
# --------------------------------------------------------------------------- #
def test_recall_proven_dominates_but_low_is_still_explored(tmp_path):
    c = _canon(tmp_path)
    hi = add_skill(c, "self", "alpha strong", grey=True)
    for _ in range(3):
        record_rating(c, hi["id"], 3, source="human")  # -> proven, high weight
    lo = add_skill(c, "self", "alpha weak", grey=True)
    record_rating(c, lo["id"], 1, source="human")  # low utility, grey
    picks = {"alpha strong": 0, "alpha weak": 0}
    for seed in range(400):
        got = recall_skills(c, "alpha", limit=1, rng=random.Random(seed))
        if got:
            picks[got[0]["object"]] += 1
    assert picks["alpha strong"] > picks["alpha weak"]  # proven wins most
    assert picks["alpha weak"] > 0  # but the low one still gets explored (non-zero floor)


def test_recall_filters_by_relevance(tmp_path):
    c = _canon(tmp_path)
    add_skill(c, "self", "brew tea", grey=False)
    add_skill(c, "self", "fix the car", grey=False)
    got = {r["object"] for r in recall_skills(c, "tea", limit=5, rng=random.Random(0))}
    assert "brew tea" in got and "fix the car" not in got


def test_recall_does_not_change_utility(tmp_path):
    c = _canon(tmp_path)
    s = add_skill(c, "self", "frequent lookup", grey=False)
    record_rating(c, s["id"], 3, source="human")
    u0 = _utility(_meta_of(c, "frequent lookup"), c.cfg)
    for seed in range(20):
        recall_skills(c, "frequent", rng=random.Random(seed))
    assert _utility(_meta_of(c, "frequent lookup"), c.cfg) == u0  # popularity never feeds rating


# --------------------------------------------------------------------------- #
# Grey decays slowly; never permanent; never auto-surfaces; region isolated   #
# --------------------------------------------------------------------------- #
def test_grey_skill_decays_slower_than_a_fact(tmp_path):
    c = _canon(tmp_path)
    c.add("user", "an ordinary fact", intensity=0.40)
    add_skill(c, "self", "a slow skill", grey=True)  # grey base == pending_intensity 0.40
    _age_entries_on_disk(c.path, days=50)
    _age_entries_on_disk(c.pending_path, days=50)
    assert c.search("ordinary fact") == []  # the fact (decay 1/90) is forgotten at 50d
    assert recall_skills(c, "slow skill", rng=random.Random(0))  # the skill (1/180) survives


def test_ungratified_grey_eventually_decays_out(tmp_path):
    c = _canon(tmp_path)
    add_skill(c, "self", "passing fancy", grey=True)
    _age_entries_on_disk(c.pending_path, days=400)  # past even the slow lease
    c.compact()
    assert _skills_in(c.pending_path) == []


def test_skills_are_never_permanent(tmp_path):
    c = _canon(tmp_path)
    s = add_skill(c, "self", "ancient craft", grey=False)
    assert s["permanent"] is False
    _age_entries_on_disk(c.path, days=9000)
    assert (
        recall_skills(c, "ancient", rng=random.Random(0)) == []
    )  # fades, can't pin itself resident


def test_skills_never_in_view_or_default_recall(tmp_path):
    c = _canon(tmp_path)
    c.add("user", "likes tea")
    add_skill(c, "self", "brew tea", grey=False)
    assert "brew tea" not in {e["object"] for e in c.view()}  # the only auto-surfacing read
    assert all(r["object"] != "brew tea" for r in c.recall("tea"))  # default recall = facts only
    assert any(r["object"] == "brew tea" for r in recall_skills(c, "tea", rng=random.Random(0)))


def test_region_isolation_fact_untouched_by_skill_ops(tmp_path):
    c = _canon(tmp_path)
    f = c.add("user", "push back", why="stood ground", emotion=0.6)
    s = add_skill(c, "user", "push back", grey=False)  # same (actor|object)
    assert f["id"] != s["id"]  # region-namespaced ids
    fact_before = next(
        e for e in _lines(c.path) if e.get("region") is None and e["what"]["object"] == "push back"
    )
    for r in (3, 1, 3):
        record_rating(c, s["id"], r, source="human")
    fact_after = next(
        e for e in _lines(c.path) if e.get("region") is None and e["what"]["object"] == "push back"
    )
    assert fact_after == fact_before  # the fact is bit-for-bit unchanged
    assert "skill" not in fact_after and "region" not in fact_after


# --------------------------------------------------------------------------- #
# review + rating priority (introspection / active learning)                  #
# --------------------------------------------------------------------------- #
def test_review_lists_library_without_using_it(tmp_path):
    c = _canon(tmp_path)
    add_skill(c, "self", "confirmed craft", grey=False)
    add_skill(c, "self", "grey hunch", grey=True)
    before = _skills_in(c.path)[0].get("recalls", 0)
    lib = {r["object"]: r for r in review_skills(c)}
    assert set(lib) == {"confirmed craft", "grey hunch"}
    assert lib["confirmed craft"]["proven"] and not lib["confirmed craft"]["must_confirm"]
    assert (not lib["grey hunch"]["proven"]) and lib["grey hunch"]["must_confirm"]
    assert _skills_in(c.path)[0].get("recalls", 0) == before  # review is reflection, not use


def test_rating_priority_asks_about_least_rated_first(tmp_path):
    c = _canon(tmp_path)
    add_skill(c, "self", "brand new", grey=True)  # 0 ratings
    seen = add_skill(c, "self", "already tried", grey=True)
    record_rating(c, seen["id"], 2, source="human")  # 1 rating
    pri = rating_priority(c)
    assert pri[0]["object"] == "brand new"  # the one with least evidence comes first


# --------------------------------------------------------------------------- #
# RatingGate — never nag                                                      #
# --------------------------------------------------------------------------- #
def test_rating_gate_cooldown(tmp_path):
    cfg = MemoryConfig()  # 600s cooldown
    g = RatingGate(tmp_path / "gate.json", cfg)
    t0 = datetime(2026, 6, 5, 9, 0, 0, tzinfo=timezone.utc)
    assert g.allow(t0) is True
    g.stamp(t0)
    assert g.allow(t0 + timedelta(seconds=300)) is False  # inside the 10-min cooldown
    assert g.allow(t0 + timedelta(seconds=601)) is True  # past it
    # cooldown survives a reload (persisted)
    g2 = RatingGate(tmp_path / "gate.json", cfg)
    assert g2.allow(t0 + timedelta(seconds=120)) is False


def test_rating_gate_daily_cap_resets_next_day(tmp_path):
    cfg = MemoryConfig()
    g = RatingGate(tmp_path / "gate.json", cfg)
    base = datetime(2026, 6, 5, 8, 0, 0, tzinfo=timezone.utc)
    for i in range(cfg.rating_daily_cap):  # spend the whole daily budget (spaced past cooldown)
        t = base + timedelta(seconds=601 * i)
        assert g.allow(t) is True
        g.stamp(t)
    over = base + timedelta(seconds=601 * cfg.rating_daily_cap)
    assert g.allow(over) is False  # capped for the day
    assert g.allow(base + timedelta(days=1)) is True  # resets next day


# --------------------------------------------------------------------------- #
# Engine pass-throughs                                                        #
# --------------------------------------------------------------------------- #
def test_engine_skill_methods(tmp_path):
    c = Canon(tmp_path / "canon.jsonl")
    eng = Engine(source=KeywordSource(), state_path=str(tmp_path / "state.json"), canon=c)
    s = add_skill(c, "self", "engine skill", grey=False)
    assert eng.record_rating(s["id"], 3, source="human")["n3"] == 1
    assert eng.record_task_rating([s["id"]], 2, source="human")
    assert any(r["object"] == "engine skill" for r in eng.recall_skills("engine"))
    assert any(r["object"] == "engine skill" for r in eng.review_skills())


def test_engine_skill_methods_noop_without_canon(tmp_path):
    eng = Engine(source=KeywordSource(), state_path=str(tmp_path / "state.json"))  # no canon
    assert eng.recall_skills("x") == []
    assert eng.record_rating("x", 3, source="human") == {}
    assert eng.record_task_rating(["x"], 3, source="human") == []
    assert eng.review_skills() == []


def test_skills_never_leak_into_the_prompt(tmp_path):
    c = Canon(tmp_path / "canon.jsonl")
    eng = Engine(source=KeywordSource(), state_path=str(tmp_path / "state.json"), canon=c)
    eng.tick([{"role": "user", "content": "hi there"}])
    s = add_skill(c, "self", "secret skill", grey=False)
    eng.record_rating(s["id"], 3, source="human")
    blk, inj = eng.render(), eng.inject("what next?")
    for needle in ("secret skill", "procedure", "utility", "rating"):
        assert needle not in blk and needle not in inj
