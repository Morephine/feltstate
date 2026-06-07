"""Gap-fill borrows (M1/M2/A1/M4), all opt-in and stance-preserving:

* M1 — evidence-weighted affect on facts: repetition still reinforces salience,
  but a repeated *flat* mention stays neutral (the catch-phrase filter) while a
  repeated *felt* one settles and gains inertia.
* M2 — importance-modulated decay curve: negative memories linger, bright ones fade.
* A1 — negative-channel mood momentum: a low mood has a trough and a slow recovery.
* M4 — an agent-called recall() tool with optional mood-congruent re-rank; it
  never injects on its own.
"""

from datetime import datetime, timezone

from feltstate.affect.traits import update_mood
from feltstate.config import MemoryConfig, MoodConfig
from feltstate.memory.canon import Canon
from feltstate.memory.feeling import blend, derive, neutral_profile, observe
from feltstate.state import AffectDelta, Mood, Traits


# --- M1: feeling (pure functions) ----------------------------------------- #
def test_observe_flat_is_neutral_charged_is_not():
    assert observe(0.0) == (0.0, 0.0, 1.0)  # flat reading -> all neutral
    pos, neg, neu = observe(0.8)
    assert pos == 0.8 and neg == 0.0 and round(neu, 4) == 0.2


def test_blend_young_moves_fast_settled_has_inertia():
    young, w_young = blend(neutral_profile(), 1.0, observe(0.8), 0.9)
    settled, w_settled = blend((0.8, 0.0, 0.2), 20.0, observe(-0.8), 0.9)
    assert derive(young)["valence"] > 0.3  # a young feeling moves a lot on one reading
    assert derive(settled)["valence"] > 0.6  # a settled feeling barely budges on one
    assert w_settled > w_young  # evidence accrued


def test_derive_charge_and_entropy():
    assert derive((0.0, 0.0, 1.0))["charge"] == 0.0  # flat = uncharged
    assert derive((0.5, 0.5, 0.0))["charge"] == 1.0  # fully loaded
    assert derive((0.45, 0.45, 0.1))["entropy"] > derive((0.9, 0.0, 0.1))["entropy"]  # ambivalent


# --- M1: canon (the headline — keep reinforce, flat stays flat) ----------- #
def test_reinforce_kept_but_flat_repeat_stays_low_charge(tmp_path):
    c = Canon(tmp_path / "canon.jsonl")
    r = {}
    for _ in range(10):  # a catch-phrase: repeated, always emotionally flat
        r = c.add("user", "haha", emotion=0.0)
    assert r["reinforced"] == 9  # reinforce STILL happens (the wanted behaviour)
    assert r["charge"] < 0.1  # but it never gains emotional weight (the filter)

    r2 = {}
    for _ in range(10):  # a real recurring feeling
        r2 = c.add("user", "thank you for being here", emotion=0.7)
    assert r2["reinforced"] == 9
    assert r2["charge"] > 0.5 and r2["valence"] > 0.5  # this one IS charged, and warm


def test_salience_charge_weight_dampens_flat_not_charged(tmp_path):
    cfg = MemoryConfig(salience_charge_weight=0.5)
    c = Canon(tmp_path / "canon.jsonl", cfg)
    flat = c.add("user", "mm-hm", intensity=0.6, emotion=0.0)
    charged = {}
    for _ in range(6):  # build the charged fact's emotional evidence over mentions
        charged = c.add("user", "you saved me", intensity=0.6, emotion=-0.8)
    assert flat["intensity"] < 0.35  # an emotionally flat fact is dimmed to ~half
    assert charged["intensity"] > flat["intensity"] * 1.5  # the charged one keeps far more


def test_fact_without_emotion_is_unaffected(tmp_path):
    c = Canon(tmp_path / "canon.jsonl")
    r = c.add("user", "an ordinary thing")  # no emotion -> no affect field
    assert r["charge"] == 0.0 and r["valence"] == 0.0


# --- M2: importance-modulated decay (negative lingers, important sticks) --- #
def test_fsrs_negative_lingers_positive_fades():
    c = Canon("unused.jsonl", MemoryConfig(decay_curve="fsrs"))
    t0 = "2026-01-01T00:00:00+00:00"
    now = datetime(2026, 1, 31, tzinfo=timezone.utc)  # 30 days on
    pos = {"ts": t0, "intensity": 0.6, "affect": {"pos": 0.7, "neg": 0.0, "neu": 0.3, "w": 5}}
    neg = {"ts": t0, "intensity": 0.6, "affect": {"pos": 0.0, "neg": 0.7, "neu": 0.3, "w": 5}}
    assert c._current_intensity(neg, now) > c._current_intensity(pos, now)


def test_fsrs_importance_slows_decay():
    c = Canon("unused.jsonl", MemoryConfig(decay_curve="fsrs"))
    t0 = "2026-01-01T00:00:00+00:00"
    now = datetime(2026, 1, 31, tzinfo=timezone.utc)
    plain = {"ts": t0, "intensity": 0.6}
    charged = {
        "ts": t0,
        "intensity": 0.6,
        "affect": {"pos": 0.0, "neg": 0.8, "neu": 0.2, "w": 5},
        "_reinforce_count": 5,
    }
    assert c._current_intensity(charged, now) > c._current_intensity(plain, now)


def test_linear_decay_is_the_default(tmp_path):
    # default curve unchanged: an old shallow fact decays additively to a floor
    c = Canon(tmp_path / "canon.jsonl")  # default decay_curve == "linear"
    e = {"ts": "2026-01-01T00:00:00+00:00", "intensity": 0.5}
    now = datetime(2026, 4, 1, tzinfo=timezone.utc)  # 90 days -> ~fully faded
    assert c._current_intensity(e, now) == 0.0


# --- A1: negative-channel mood momentum ----------------------------------- #
def _run_mood(mu, deltas):
    traits = Traits()  # neutral temperament -> no trait gravity, clean test
    cfg = MoodConfig(momentum_mu=mu)
    m = Mood(valence=0.0)
    for d in deltas:
        m = update_mood(m, d, traits, cfg)
    return m


def test_momentum_makes_a_low_mood_linger():
    neg = AffectDelta(valence=-0.6, arousal=0.5)
    neu = AffectDelta(valence=0.0, arousal=0.4)
    seq = [neg, neg, neg, neu, neu, neu]  # sink, then let it recover
    assert _run_mood(0.5, seq).valence < _run_mood(0.0, seq).valence  # momentum stays lower


def test_momentum_recovers_no_lock_in():
    traits = Traits()
    cfg = MoodConfig(momentum_mu=0.5)
    m = Mood(valence=-0.8, velocity=-0.1)
    neu = AffectDelta(valence=0.0, arousal=0.4)
    for _ in range(60):
        m = update_mood(m, neu, traits, cfg)
    assert m.valence > -0.05  # climbs back to ~neutral; never locks in


def test_momentum_zero_is_plain_ewma():
    traits = Traits()
    cfg0 = MoodConfig(momentum_mu=0.0)
    m = update_mood(Mood(valence=0.2), AffectDelta(valence=-0.6, arousal=0.5), traits, cfg0)
    assert abs(m.valence - 0.04) < 1e-9  # 0.2*0.8 + (-0.6)*0.2, no gravity
    assert m.velocity == 0.0


# --- M4: agent-called recall with mood-congruent re-rank ------------------ #
def test_recall_is_mood_congruent(tmp_path):
    c = Canon(tmp_path / "canon.jsonl")
    c.add("user", "a sunny day out", emotion=0.8)
    c.add("user", "slipped over on a rainy day", emotion=-0.8)
    assert (
        c.recall("day", mood=-0.7)[0]["object"] == "slipped over on a rainy day"
    )  # sad surfaces sad
    assert c.recall("day", mood=0.7)[0]["object"] == "a sunny day out"  # bright surfaces bright


def test_recall_bumps_recalls_and_returns_a_list(tmp_path):
    c = Canon(tmp_path / "canon.jsonl")
    c.add("user", "something worth remembering")
    r = c.recall("worth remembering")
    assert isinstance(r, list) and len(r) == 1
    assert r[0]["recalls"] == 1  # used memory sticks; recall is the agent's tool


def test_recall_object_type_filter(tmp_path):
    c = Canon(tmp_path / "canon.jsonl")
    c.add("user", "apple", action="eat")
    c.add("user", "movie", action="watch")
    objs = [x["object"] for x in c.recall("", object_type="eat")]
    assert "apple" in objs and "movie" not in objs


# --- M3: bi-temporal (a belief that changed is kept, not erased) ---------- #
def test_correct_records_a_validity_window(tmp_path):
    c = Canon(tmp_path / "canon.jsonl")
    c.add("user", "working at company A", action="work")
    c.correct("work", object="working at company B", action="work")
    hist = {h["object"]: h for h in c.history("company")}
    assert hist["working at company A"]["status"] == "superseded"
    assert hist["working at company B"]["status"] == "active"
    assert hist["working at company A"]["invalid_at"] is not None  # the old belief has an end
    assert hist["working at company B"]["invalid_at"] is None  # the current one is still open


def test_as_of_returns_the_belief_held_then(tmp_path):
    import json

    p = tmp_path / "canon.jsonl"
    rows = [  # one belief, two versions: A valid Jan–Mar, then B from Mar on
        {
            "ts": "2026-01-01T00:00:00+00:00",
            "who": {"actor": "u"},
            "what": {"action": "work", "object": "company A"},
            "valid_at": "2026-01-01T00:00:00+00:00",
            "invalid_at": "2026-03-01T00:00:00+00:00",
            "_superseded_by": "xx",
            "intensity": 0.5,
        },
        {
            "ts": "2026-03-01T00:00:00+00:00",
            "who": {"actor": "u"},
            "what": {"action": "work", "object": "company B"},
            "valid_at": "2026-03-01T00:00:00+00:00",
            "intensity": 0.5,
        },
    ]
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    c = Canon(p)
    assert [x["object"] for x in c.as_of("company", "2026-02-01T00:00:00+00:00")] == ["company A"]
    assert [x["object"] for x in c.as_of("company", "2026-04-01T00:00:00+00:00")] == ["company B"]
