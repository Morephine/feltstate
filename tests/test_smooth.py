"""Label hysteresis — a new top label must persist a few ticks before it replaces
the shown one, so a noisy source can't flicker the rendered block."""

from feltstate.affect.smooth import smooth_labels
from feltstate.engine import Engine
from feltstate.sources.base import AffectSource
from feltstate.state import AffectDelta


def test_first_label_commits_immediately():
    shown, cand, streak = smooth_labels(["happy"], [], None, 0, 2)
    assert shown == ["happy"] and cand is None and streak == 0


def test_stable_top_accepts_full_new_list():
    shown, cand, streak = smooth_labels(["happy", "calm"], ["happy"], None, 0, 2)
    assert shown == ["happy", "calm"] and cand is None and streak == 0


def test_new_top_must_persist_before_switching():
    # first sighting of a new top -> held, candidate noted
    shown, cand, streak = smooth_labels(["sad"], ["happy"], None, 0, 2)
    assert shown == ["happy"] and cand == "sad" and streak == 1
    # it persists a second tick -> the switch commits
    shown, cand, streak = smooth_labels(["sad"], ["happy"], "sad", 1, 2)
    assert shown == ["sad"] and cand is None and streak == 0


def test_candidate_resets_when_the_challenger_changes():
    shown, cand, streak = smooth_labels(["sad"], ["happy"], "angry", 1, 2)
    assert shown == ["happy"] and cand == "sad" and streak == 1


def test_empty_reading_holds_what_was_shown():
    shown, _, _ = smooth_labels([], ["happy"], None, 0, 2)
    assert shown == ["happy"]


def test_n_of_one_disables_hysteresis():
    shown, _, _ = smooth_labels(["sad"], ["happy"], None, 0, 1)
    assert shown == ["sad"]


class _Scripted(AffectSource):
    def __init__(self, deltas):
        self.deltas = deltas
        self.i = 0

    def read(self, messages, *, baseline, persona=""):
        d = self.deltas[min(self.i, len(self.deltas) - 1)]
        self.i += 1
        return d


def test_engine_holds_label_through_a_one_tick_blip(tmp_path):
    deltas = [
        AffectDelta(labels=["content"], confidence=0.8),
        AffectDelta(labels=["content"], confidence=0.8),
        AffectDelta(labels=["frustrated"], confidence=0.8),  # a single-tick blip
        AffectDelta(labels=["content"], confidence=0.8),
    ]
    eng = Engine(source=_Scripted(deltas), state_path=tmp_path / "s.json")
    eng.tick([{"role": "user", "content": "x"}])  # content commits
    eng.tick([{"role": "user", "content": "x"}])  # content stable
    eng.tick([{"role": "user", "content": "x"}])  # frustrated for one tick -> held
    assert eng.state.mood.labels[0] == "content"  # the blip did not flip the shown top
