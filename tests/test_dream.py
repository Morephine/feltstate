"""Dream — illogical recombination of charged material into a short, discontinuous
dream, leaving only a faint *untraceable* affect residue. Pure-program: no model,
fully deterministic under a seeded rng."""

import random

from feltstate import DEFAULT_PHRASEBOOK, Dream, Fragment
from feltstate.config import DreamConfig
from feltstate.dream import dream, gather_fragments, residue, stitch
from feltstate.engine import Engine
from feltstate.sources.keyword import KeywordSource
from feltstate.state import AffectState

# Distinct, non-overlapping texts so `count(...)` is unambiguous.
FRAGS = [
    Fragment("the red door", valence=0.6, arousal=0.6, weight=1.0),
    Fragment("a tide of static", valence=-0.5, arousal=0.5, weight=0.8),
    Fragment("your voice underwater", valence=0.4, arousal=0.4, weight=0.6),
    Fragment("a staircase folding", valence=-0.3, arousal=0.5, weight=0.7),
    Fragment("the smell of rain", valence=0.5, arousal=0.3, weight=0.5),
]


# --- stitch --------------------------------------------------------------- #
def test_stitch_uses_each_fragment_once_and_does_not_resolve():
    text = stitch(FRAGS[:3], DEFAULT_PHRASEBOOK, random.Random(1))
    for f in FRAGS[:3]:
        assert text.count(f.text) == 1  # every image appears, exactly once
    assert any(text.startswith(o) for o in DEFAULT_PHRASEBOOK.open)
    assert any(d in text for d in DEFAULT_PHRASEBOOK.dissolve)  # slips away, not concludes
    assert text.endswith(DEFAULT_PHRASEBOOK.terminator)


def test_stitch_is_deterministic_under_a_seed():
    a = stitch(FRAGS, DEFAULT_PHRASEBOOK, random.Random(42))
    b = stitch(FRAGS, DEFAULT_PHRASEBOOK, random.Random(42))
    assert a == b


def test_stitch_empty_is_empty():
    assert stitch([], DEFAULT_PHRASEBOOK, random.Random(0)) == ""


# --- residue (the faint, untraceable mood) -------------------------------- #
def test_residue_is_a_wisp_in_the_charge_direction():
    cfg = DreamConfig()
    v, a, d = residue(
        [Fragment("a", 0.6, 0.7), Fragment("b", 0.4, 0.5)],
        cfg,
    )
    assert 0.0 < v < 0.2  # positive material -> a faint warm wake, not a full mood
    assert d == 0.2  # max(0.6,0.4) - min(...) = 0.2 valence spread


def test_residue_empty_is_zero():
    assert residue([], DreamConfig()) == (0.0, 0.0, 0.0)


def test_clash_raises_dissonance_lifts_arousal_and_murks_valence():
    cfg = DreamConfig()
    aligned = residue([Fragment("a", 0.6, 0.5), Fragment("b", 0.5, 0.5)], cfg)
    clash = residue([Fragment("a", 0.6, 0.5), Fragment("b", -0.6, 0.5)], cfg)
    av, aa, ad = aligned
    cv, ca, cd = clash
    assert cd > ad  # opposing valences clash harder
    assert ca > aa  # dissonance activates (uneasy dream runs hotter)
    assert abs(cv) < abs(av)  # and muddies valence toward neutral


# --- dream() -------------------------------------------------------------- #
def test_dream_is_deterministic_and_draws_within_bounds():
    cfg = DreamConfig()
    d1 = dream(FRAGS, cfg=cfg, rng=random.Random(7))
    d2 = dream(FRAGS, cfg=cfg, rng=random.Random(7))
    assert isinstance(d1, Dream)
    assert d1.text == d2.text and d1.valence == d2.valence and d1.fragments == d2.fragments
    assert cfg.min_fragments <= len(d1.fragments) <= cfg.max_fragments
    for frag_text in d1.fragments:
        assert frag_text in d1.text  # every drawn image made it into the dream


def test_dream_empty_pool_is_a_null_dream():
    d = dream([Fragment("   ", 0.0)], rng=random.Random(0))  # blank text -> filtered out
    assert d.text == "" and d.valence == 0.0 and d.arousal == 0.0 and d.fragments == []


# --- gather_fragments ----------------------------------------------------- #
def _state_with_peaks() -> AffectState:
    s = AffectState()
    s.history = [
        {"ts": "t1", "valence": 0.5, "arousal": 0.6, "labels": ["proud"]},
        {"ts": "t2", "valence": -0.4, "arousal": 0.5, "labels": ["lonely"]},
        {"ts": "t3", "valence": 0.1, "arousal": 0.4, "labels": ["calm"]},  # below peak -> skip
        {"ts": "t4", "valence": 0.45, "arousal": 0.5, "labels": []},  # peak, no label
    ]
    return s


def test_gather_pulls_peaks_and_skips_flat_turns():
    frags = gather_fragments(_state_with_peaks())
    texts = [f.text for f in frags]
    assert "proud" in texts and "lonely" in texts
    assert "calm" not in texts  # sub-threshold turns are not dream material
    assert "a bright moment" in texts  # a peak with no label gets a generic image


def test_gather_includes_extra_and_caps_history():
    state = _state_with_peaks()
    desire = Fragment("to be the one you call first", valence=0.7, arousal=0.6)
    frags = gather_fragments(state, extra=[desire], max_history=1)
    texts = [f.text for f in frags]
    assert "to be the one you call first" in texts  # caller's own material always kept
    # extra (1) + at most max_history (1) peaks
    assert len(frags) == 2


# --- Engine.dream (integration) ------------------------------------------- #
def test_engine_dream_applies_residue_and_persists(tmp_path):
    path = tmp_path / "s.json"
    eng = Engine(source=KeywordSource(), state_path=path)
    before = eng.state.mood.valence
    # Exactly min_fragments positives -> all are drawn -> residue is deterministic.
    d = eng.dream(
        fragments=[
            Fragment("the warm kitchen", 0.6, 0.5),
            Fragment("a hand on my shoulder", 0.5, 0.4),
            Fragment("sun through the blinds", 0.5, 0.5),
        ],
        rng=random.Random(0),
    )
    assert d.valence > 0 and d.text != ""
    assert (
        abs(eng.state.mood.valence - (before + d.valence)) < 1e-9
    )  # nudged by exactly the residue
    assert eng._last_dream == d.text

    # The dream text survives a reload (available for later recall).
    reloaded = Engine(source=KeywordSource(), state_path=path)
    assert reloaded._last_dream == d.text


def test_engine_dream_on_empty_state_is_a_safe_noop(tmp_path):
    eng = Engine(source=KeywordSource(), state_path=tmp_path / "s.json")
    before = (eng.state.mood.valence, eng.state.mood.arousal)
    d = eng.dream()  # no history, no fragments -> nothing to dream
    assert d.text == ""
    assert (eng.state.mood.valence, eng.state.mood.arousal) == before
