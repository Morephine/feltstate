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


# --- edge-case / invariant tests ------------------------------------------


def test_returned_list_is_a_copy_of_committed():
    # The caller must be able to mutate the returned list without corrupting
    # the internal committed state it passed in.
    committed = ["happy"]
    shown, _, _ = smooth_labels([], committed, None, 0, 2)
    shown.append("extra")
    assert committed == ["happy"]  # original untouched


def test_returned_list_is_a_copy_of_new_labels_on_commit():
    new_labels = ["sad", "tired"]
    shown, _, _ = smooth_labels(new_labels, ["happy"], "sad", 1, 2)
    shown.append("extra")
    assert new_labels == ["sad", "tired"]  # original untouched


def test_empty_committed_with_no_new_labels_returns_empty():
    # Both empty — nothing to hold onto, nothing new: return empty list, no candidate.
    shown, cand, streak = smooth_labels([], [], None, 0, 2)
    assert shown == [] and cand is None and streak == 0


def test_streak_accumulates_over_ticks_until_threshold():
    # n=3: a challenger needs 3 consecutive ticks.
    shown1, cand1, streak1 = smooth_labels(["sad"], ["happy"], None, 0, 3)
    assert shown1 == ["happy"] and cand1 == "sad" and streak1 == 1
    shown2, cand2, streak2 = smooth_labels(["sad"], ["happy"], cand1, streak1, 3)
    assert shown2 == ["happy"] and cand2 == "sad" and streak2 == 2
    # Third tick: streak reaches n=3 -> commit.
    shown3, cand3, streak3 = smooth_labels(["sad"], ["happy"], cand2, streak2, 3)
    assert shown3 == ["sad"] and cand3 is None and streak3 == 0


def test_challenger_interruption_resets_streak_to_one():
    # Challenger A has streak 2, then B takes over: B becomes the new candidate
    # at streak 1, not inheriting A's streak.
    shown, cand, streak = smooth_labels(["B"], ["happy"], "A", 2, 3)
    assert shown == ["happy"] and cand == "B" and streak == 1


def test_n_zero_disables_hysteresis_same_as_n_one():
    # n <= 1 disables hysteresis: max(1, 0) == 1 -> commits on first sighting.
    shown, cand, streak = smooth_labels(["sad"], ["happy"], None, 0, 0)
    assert shown == ["sad"] and cand is None and streak == 0


def test_secondary_labels_update_when_top_is_stable():
    # If the top label is unchanged, the full new list (including secondaries) is adopted.
    shown, cand, streak = smooth_labels(
        ["happy", "curious", "calm"],
        ["happy", "tired"],
        None,
        0,
        2,
    )
    assert shown == ["happy", "curious", "calm"]
    assert cand is None and streak == 0


def test_secondary_labels_held_when_top_is_challenged():
    # While the top is still being challenged, we keep the old full committed list
    # (secondaries included), not just the old top.
    shown, cand, streak = smooth_labels(
        ["sad", "tired"],
        ["happy", "calm"],
        None,
        0,
        2,
    )
    assert shown == ["happy", "calm"]  # entire old list held
    assert cand == "sad" and streak == 1


def test_large_n_holds_indefinitely_until_threshold():
    # With n=5, four ticks of the same challenger still don't commit.
    committed = ["happy"]
    cand, streak = None, 0
    for _ in range(4):
        shown, cand, streak = smooth_labels(["sad"], committed, cand, streak, 5)
        assert shown == ["happy"]  # not committed yet
    # Fifth tick commits.
    shown, cand, streak = smooth_labels(["sad"], committed, cand, streak, 5)
    assert shown == ["sad"] and cand is None and streak == 0
