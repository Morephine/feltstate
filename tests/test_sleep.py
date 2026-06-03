"""Sleep pressure — the single tiredness accumulator that decides *when* to dream:
rises with arousal×time, gated by threshold + idle + a hard refractory floor,
resets on a dream. Plus the Engine.maybe_dream wiring and dream-forgetting."""

import random
from datetime import datetime, timedelta

from feltstate import Engine, Fragment, Tiredness, TirednessConfig
from feltstate.sources.keyword import KeywordSource

T0 = datetime(2026, 1, 1, 8, 0, 0)


def _at(**kw) -> datetime:
    return T0 + timedelta(**kw)


# --- rise ----------------------------------------------------------------- #
def test_first_rise_is_just_a_stamp():
    cfg = TirednessConfig()
    t = Tiredness()
    t.rise(1.0, T0, cfg)
    assert t.level == 0.0  # nothing to integrate yet
    assert t.last_update_ts == T0.isoformat()


def test_rise_accrues_arousal_times_elapsed():
    cfg = TirednessConfig()  # rise_k = 0.125
    t = Tiredness()
    t.rise(1.0, T0, cfg)
    t.rise(1.0, _at(hours=1), cfg)
    assert abs(t.level - 0.125) < 1e-9  # 0.125 * arousal 1.0 * 1h


def test_higher_arousal_tires_faster():
    cfg = TirednessConfig()
    hot, calm = Tiredness(), Tiredness()
    for t, ar in ((hot, 0.9), (calm, 0.3)):
        t.rise(ar, T0, cfg)
        t.rise(ar, _at(hours=2), cfg)
    assert hot.level > calm.level  # an activated stretch builds pressure faster


def test_self_acceleration_compounds_when_enabled():
    cfg = TirednessConfig(self_accel_alpha=1.0)
    t = Tiredness()
    t.rise(1.0, T0, cfg)
    t.rise(1.0, _at(hours=1), cfg)  # step 1: +0.125
    t.rise(1.0, _at(hours=2), cfg)  # step 2: bigger, level feeds the rate
    assert t.level > 0.25  # > two flat steps; exhaustion spirals


def test_level_is_capped_across_a_long_idle():
    cfg = TirednessConfig()
    t = Tiredness()
    t.rise(1.0, T0, cfg)
    t.rise(1.0, _at(hours=1000), cfg)  # would be 125; capped
    assert t.level == cfg.level_cap


# --- ready (all three conditions) ----------------------------------------- #
def test_ready_requires_threshold_idle_and_refractory():
    cfg = TirednessConfig()  # threshold 1.0, idle_gate 30, refractory 10h
    now = _at(hours=20)

    tired = Tiredness(level=1.5)
    assert tired.ready(now, idle_minutes=60, cfg=cfg) is True

    assert tired.ready(now, idle_minutes=10, cfg=cfg) is False  # not alone long enough
    assert Tiredness(level=0.5).ready(now, 60, cfg) is False  # not tired enough

    just_slept = Tiredness(level=1.5, last_dream_ts=_at(hours=15).isoformat())
    assert just_slept.ready(now, 60, cfg) is False  # 5h < 10h refractory


def test_discharge_resets_and_starts_refractory():
    cfg = TirednessConfig()
    now = _at(hours=20)
    t = Tiredness(level=2.0)
    t.discharge(now)
    assert t.level == 0.0
    assert t.last_dream_ts == now.isoformat()
    # immediately after a dream it cannot dream again (refractory + drained)
    t.level = 1.5
    assert t.ready(now, 60, cfg) is False


def test_round_trip():
    t = Tiredness(
        level=1.2345, last_dream_ts=T0.isoformat(), last_update_ts=_at(hours=3).isoformat()
    )
    assert Tiredness.from_dict(t.to_dict()).to_dict() == t.to_dict()


# --- Engine.maybe_dream (the when) ---------------------------------------- #
_FRAGS = [
    Fragment("the warm kitchen", 0.6, 0.5),
    Fragment("a hand on my shoulder", 0.5, 0.4),
    Fragment("sun through the blinds", 0.5, 0.5),
]


def test_maybe_dream_returns_none_when_not_tired(tmp_path):
    eng = Engine(source=KeywordSource(), state_path=tmp_path / "s.json")
    assert eng.maybe_dream(idle_minutes=60, now=_at(hours=20)) is None  # fresh -> level 0


def test_maybe_dream_fires_when_ready_then_discharges(tmp_path):
    path = tmp_path / "s.json"
    eng = Engine(source=KeywordSource(), state_path=path)
    eng.tiredness.level = 1.5  # force tired
    before = eng.state.mood.valence
    now = _at(hours=20)

    d = eng.maybe_dream(idle_minutes=60, now=now, fragments=_FRAGS, rng=random.Random(0))
    assert d is not None and d.text != ""
    assert eng.tiredness.level == 0.0  # discharged on sleep
    assert eng.tiredness.last_dream_ts == now.isoformat()
    assert eng.state.mood.valence > before  # the residue was applied
    assert eng._last_dream == d.text

    # discharge persisted across a reload
    assert Engine(source=KeywordSource(), state_path=path).tiredness.level == 0.0


def test_refractory_blocks_a_second_dream_the_same_night(tmp_path):
    eng = Engine(source=KeywordSource(), state_path=tmp_path / "s.json")
    eng.tiredness.level = 1.5
    assert eng.maybe_dream(
        idle_minutes=60, now=_at(hours=20), fragments=_FRAGS, rng=random.Random(0)
    )
    eng.tiredness.level = 1.5  # tired again immediately
    # only 1h later — inside the 10h refractory floor
    assert eng.maybe_dream(idle_minutes=60, now=_at(hours=21), fragments=_FRAGS) is None


def test_dream_is_forgotten_once_its_mood_decays(tmp_path):
    eng = Engine(source=KeywordSource(), state_path=tmp_path / "s.json")
    eng.tiredness.level = 1.5
    d = eng.maybe_dream(
        idle_minutes=60,
        now=_at(hours=20),
        fragments=[Fragment("x", 0.7, 0.6), Fragment("y", 0.6, 0.5), Fragment("z", 0.6, 0.5)],
        rng=random.Random(0),
    )
    assert eng._last_dream == d.text and d.text != ""
    # quiet, cue-less turns let the residue decay back to baseline...
    for _ in range(40):
        eng.tick([{"role": "user", "content": "the wooden table is brown"}])
    assert eng._last_dream == ""  # ...and the dream is forgotten with the mood
