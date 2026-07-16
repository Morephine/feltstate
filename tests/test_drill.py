"""Tests for the fingerprint-genealogy drill (provenance traversal to leaves)."""

from __future__ import annotations

from feltstate.memory.lifecycle import (
    affect_trail,
    drill,
    fuse,
    leaf_pointers,
    make_fingerprint,
    make_source_ptr,
    trace_contexts,
    trace_memory,
    verify_source_ptr,
)

T1 = "2026-07-01T10:05:00+00:00"
T2 = "2026-07-01T14:05:00+00:00"
TP = "2026-07-02T09:00:00+00:00"


def _leaf(mid: str, when: str, valence: float, text: str) -> dict:
    ptr = make_source_ptr("chat/2026-07-01.json", when, when, text)
    return make_fingerprint([ptr], {"valence": valence, "arousal": 0.4}, when, mid=mid)


def test_drill_walks_lineage_to_leaves():
    fp1 = _leaf("m1", T1, 0.6, "we finally shipped it")
    fp2 = _leaf("m2", T2, -0.2, "it broke then got fixed")
    parent = fuse([fp1, fp2], {"valence": 0.3, "arousal": 0.5}, TP, mid="p1")
    store = {"m1": fp1, "m2": fp2}

    tree = drill(parent, store.get)
    assert tree["mid"] == "p1"
    assert tree["verified"] is True
    assert {c["mid"] for c in tree["children"]} == {"m1", "m2"}
    # each child is a leaf (no further ancestry)
    assert all(not c["children"] and not c["sources"] for c in tree["children"])

    leaves = leaf_pointers(tree)
    assert len(leaves) == 2
    assert {p["sha"] for p in leaves} == {
        fp1["core"]["source_ptrs"][0]["sha"],
        fp2["core"]["source_ptrs"][0]["sha"],
    }


def test_drill_records_pruned_branch_without_crashing():
    fp1 = _leaf("m1", T1, 0.0, "x")
    parent = fuse([fp1], {"valence": 0.0, "arousal": 0.4}, TP, mid="p1")
    # the store no longer has m1 -> a lost branch (instinct memory), not an error
    tree = drill(parent, lambda _mid: None)
    assert tree["lost"] == ["m1"]
    assert tree["children"] == []
    # the ancestor still names the original material in its own copied-up core
    assert leaf_pointers(tree)  # non-empty: the fused core still carries the pointer


def test_partial_lost_branch_keeps_its_copied_source_pointer():
    fp1 = _leaf("m1", T1, 0.1, "first branch")
    fp2 = _leaf("m2", T2, -0.1, "lost branch")
    parent = fuse([fp1, fp2], {"valence": 0.0, "arousal": 0.4}, TP, mid="p1")
    tree = drill(parent, {"m1": fp1}.get)

    assert tree["lost"] == ["m2"]
    assert {p["sha"] for p in leaf_pointers(tree)} == {
        fp1["core"]["source_ptrs"][0]["sha"],
        fp2["core"]["source_ptrs"][0]["sha"],
    }


def test_drill_is_cycle_safe():
    fp = _leaf("a", T1, 0.0, "x")
    fp["lineage"] = ["a"]  # a self-referential cycle
    tree = drill(fp, {"a": fp}.get)
    assert tree["mid"] == "a"
    assert tree["children"][0].get("revisited") is True  # walked once, then stubbed


def test_affect_trail_captures_mood_at_each_node():
    fp1 = _leaf("m1", T1, 0.6, "x")
    parent = fuse([fp1], {"valence": 0.3, "arousal": 0.5}, TP, mid="p1")
    trail = affect_trail(drill(parent, {"m1": fp1}.get))
    assert [n["mid"] for n in trail] == ["p1", "m1"]
    assert trail[0]["birth_affect"]["valence"] == 0.3
    assert trail[1]["birth_affect"]["valence"] == 0.6


def test_depth_bound_truncates():
    fp1 = _leaf("m1", T1, 0.0, "x")
    parent = fuse([fp1], {"valence": 0.0, "arousal": 0.4}, TP, mid="p1")
    tree = drill(parent, {"m1": fp1}.get, max_depth=1)
    # depth 0 = parent, depth 1 = child stub (truncated before expanding further)
    assert tree["children"][0].get("truncated") is True


def test_drill_walks_src_edge_not_only_lineage():
    leaf = _leaf("s1", T1, 0.2, "the source moment")
    ptr = make_source_ptr("chat/2026-07-02.json", TP, TP, "a note derived from s1")
    derived = make_fingerprint([ptr], {"valence": 0.1, "arousal": 0.3}, TP, mid="d1", src=["s1"])
    tree = drill(derived, {"s1": leaf}.get)
    assert [s["mid"] for s in tree["sources"]] == ["s1"]  # src edge is followed
    assert tree["children"] == []  # nothing on the lineage edge


def test_drill_three_levels_deep():
    a = _leaf("a", T1, 0.5, "alpha")
    b = _leaf("b", T2, -0.1, "beta")
    mid1 = fuse([a, b], {"valence": 0.2, "arousal": 0.4}, TP, mid="mid1")
    c = _leaf("c", T2, 0.3, "gamma")
    top = fuse([mid1, c], {"valence": 0.25, "arousal": 0.5}, TP, mid="top")
    store = {"a": a, "b": b, "mid1": mid1, "c": c}

    tree = drill(top, store.get)
    assert {k["mid"] for k in tree["children"]} == {"mid1", "c"}
    mid1_node = next(k for k in tree["children"] if k["mid"] == "mid1")
    assert {g["mid"] for g in mid1_node["children"]} == {"a", "b"}
    # only the true leaves' pointers surface, not the copied-up unions on mid nodes
    shas = {p["sha"] for p in leaf_pointers(tree)}
    assert shas == {
        a["core"]["source_ptrs"][0]["sha"],
        b["core"]["source_ptrs"][0]["sha"],
        c["core"]["source_ptrs"][0]["sha"],
    }


def test_drill_diamond_genealogy_dedups_leaves():
    shared = _leaf("shared", T1, 0.0, "the shared root moment")
    left = fuse([shared], {"valence": 0.1, "arousal": 0.4}, TP, mid="left")
    right = fuse([shared], {"valence": -0.1, "arousal": 0.4}, TP, mid="right")
    top = fuse([left, right], {"valence": 0.0, "arousal": 0.5}, TP, mid="top")
    store = {"shared": shared, "left": left, "right": right}

    tree = drill(top, store.get)
    left_node = next(k for k in tree["children"] if k["mid"] == "left")
    right_node = next(k for k in tree["children"] if k["mid"] == "right")
    stubs = [n for n in left_node["children"] + right_node["children"] if n["mid"] == "shared"]
    assert any(s.get("revisited") for s in stubs)  # reached twice, expanded once
    # the shared leaf's pointer appears exactly once despite the diamond
    shas = [p["sha"] for p in leaf_pointers(tree)]
    assert shas == [shared["core"]["source_ptrs"][0]["sha"]]


def test_same_text_at_two_times_remains_two_evidence_pointers():
    left = _leaf("left", T1, 0.0, "same words")
    right = _leaf("right", T2, 0.0, "same words")
    top = fuse([left, right], {"valence": 0.0, "arousal": 0.4}, TP, mid="top")
    pointers = leaf_pointers(drill(top, {"left": left, "right": right}.get))
    assert len(pointers) == 2
    assert {p["t0"] for p in pointers} == {T1, T2}


def test_trace_contexts_bridges_leaf_pointer_to_transcript_window():
    leaf = _leaf("m1", T1, 0.2, "the exact source")
    tree = drill(leaf, {}.get)
    turns = [
        {"role": "user", "content": "before", "timestamp": "2026-07-01T10:04:00+00:00"},
        {"role": "assistant", "content": "source", "timestamp": T1},
        {"role": "user", "content": "after", "timestamp": "2026-07-01T10:06:00+00:00"},
    ]

    traced = trace_contexts(tree, lambda _file: turns, before=1, after=1)
    assert len(traced) == 1
    assert traced[0]["context"]["ok"] is True
    assert [t["content"] for t in traced[0]["context"]["turns"]] == [
        "before",
        "source",
        "after",
    ]


def test_tampered_core_shows_unverified():
    fp = _leaf("m1", T1, 0.5, "original")
    assert drill(fp, {}.get)["verified"] is True
    fp["core"]["birth_affect"]["valence"] = 0.9  # mutate the sealed core
    assert drill(fp, {}.get)["verified"] is False


def test_trace_contexts_uses_full_pointer_range_and_can_verify_source_text():
    exact = "first source line\nsecond source line"
    ptr = make_source_ptr(
        "chat/range.json",
        "2026-07-01T10:01:00+00:00",
        "2026-07-01T10:02:00+00:00",
        exact,
    )
    fp = make_fingerprint([ptr], {"valence": 0.1}, T1, mid="range")
    tree = drill(fp, {}.get)
    turns = [
        {"role": "user", "content": "before", "timestamp": "2026-07-01T10:00:00+00:00"},
        {"role": "user", "content": "first", "timestamp": "2026-07-01T10:01:00+00:00"},
        {"role": "assistant", "content": "second", "timestamp": "2026-07-01T10:02:00+00:00"},
        {"role": "user", "content": "after", "timestamp": "2026-07-01T10:03:00+00:00"},
    ]

    traced = trace_contexts(
        tree,
        lambda _file: turns,
        before=0,
        after=0,
        load_source_text=lambda _pointer: exact,
    )
    assert [t["content"] for t in traced[0]["context"]["source_turns"]] == [
        "first",
        "second",
    ]
    assert traced[0]["source_verified"] is True
    assert verify_source_ptr(ptr, exact)


def test_trace_memory_returns_one_inspectable_report():
    exact = "the source moment"
    leaf = _leaf("m1", T1, 0.2, exact)
    turns = [{"role": "user", "content": exact, "timestamp": T1}]
    report = trace_memory(
        leaf,
        {}.get,
        lambda _file: turns,
        before=0,
        after=0,
        load_source_text=lambda _pointer: exact,
    )
    assert report["tree"]["verified"] is True
    assert report["n_leaf_pointers"] == 1
    assert report["n_contexts_ok"] == 1
    assert report["n_sources_verified"] == 1
    assert report["n_lost_branches"] == 0
    assert report["affect_trail"][0]["mid"] == "m1"


def test_trace_contexts_records_exact_text_loader_failure_per_branch():
    leaf = _leaf("m1", T1, 0.2, "source")
    tree = drill(leaf, {}.get)
    turns = [{"role": "user", "content": "source", "timestamp": T1}]

    def broken(_pointer):
        raise OSError("archive unavailable")

    traced = trace_contexts(tree, lambda _file: turns, load_source_text=broken)
    assert traced[0]["context"]["ok"] is True
    assert traced[0]["source_verified"] is None
    assert "archive unavailable" in traced[0]["verification_error"]
