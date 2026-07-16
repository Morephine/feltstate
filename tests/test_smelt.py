"""Tests for the crystal smelt core (verify → heat → seal)."""

from __future__ import annotations

from feltstate.memory.lifecycle import (
    SmeltConfig,
    born_heat,
    drill,
    leaf_pointers,
    make_fingerprint,
    make_source_ptr,
    smelt,
    verify_fingerprint,
)

TS = "2026-07-02T09:00:00+00:00"
AFFECT = {"valence": 0.6, "arousal": 0.5}


def _row(actor: str, action: str, obj: str, intensity: float = 0.6) -> dict:
    return {
        "who": {"actor": actor},
        "what": {"action": action, "object": obj},
        "intensity": intensity,
    }


ROWS = [
    _row("rin", "shipped", "the release it finally worked", 0.8),
    _row("rin", "fixed", "the broken build", 0.5),
]
PTRS = [
    make_source_ptr(
        "chat/07-01.json",
        "2026-07-01T10:00:00+00:00",
        "2026-07-01T10:05:00+00:00",
        "we shipped the release and it worked",
    ),
    make_source_ptr(
        "chat/07-01.json",
        "2026-07-01T14:00:00+00:00",
        "2026-07-01T14:05:00+00:00",
        "fixed the broken build",
    ),
]

GOOD = "rin shipped the release, it worked, and fixed the broken build"


def test_smelt_accepts_faithful_summary():
    r = smelt(
        GOOD, ROWS, birth_affect=AFFECT, ts_utc=TS, mid="c1", source_ptrs=PTRS, src_ids=["m1", "m2"]
    )
    assert r["verdict"] == "accept"
    c = r["crystal"]
    assert c["mid"] == "c1"
    assert 0 < c["heat"] <= 0.9
    assert c["suspect"] is None
    assert verify_fingerprint(c["fingerprint"]) is True  # born sealed and verifiable


def test_smelt_rejects_fabrication():
    r = smelt(
        "rin flew to mars and drank 7 coffees with aliens",
        ROWS,
        birth_affect=AFFECT,
        ts_utc=TS,
        mid="c2",
        source_ptrs=PTRS,
    )
    assert r["verdict"] == "reject"
    assert r["crystal"] is None  # not committed; the sources stay live


def test_smelt_suspect_discounts_heat():
    full = smelt(GOOD, ROWS, birth_affect=AFFECT, ts_utc=TS, mid="c3", source_ptrs=PTRS)
    susp = smelt(
        GOOD + " in 2099", ROWS, birth_affect=AFFECT, ts_utc=TS, mid="c4", source_ptrs=PTRS
    )
    assert susp["verdict"] == "suspect"
    assert susp["crystal"]["suspect"] == ["numbers"]
    assert susp["crystal"]["heat"] < full["crystal"]["heat"]


def test_born_heat_uses_mean_peak_count_and_cap():
    assert born_heat([_row("k", "a", "b", 0.9)]) > born_heat([_row("k", "a", "b", 0.2)])
    strong = [_row("k", "a", "b", 1.0) for _ in range(5)]
    assert born_heat(strong, milestone=True) >= born_heat(strong, milestone=False)
    assert born_heat(strong, milestone=False) <= 0.9  # default cap holds


def test_smelt_fails_closed_on_unusable_pointers_by_default():
    r = smelt(GOOD, ROWS, birth_affect=AFFECT, ts_utc=TS, mid="c5", source_ptrs=[])
    assert r["verdict"] == "reject"
    assert r["crystal"] is None
    assert "fingerprint" in r["fails"]


def test_smelt_can_explicitly_allow_unsealed_fallback():
    cfg = SmeltConfig(require_fingerprint=False)
    r = smelt(
        GOOD,
        ROWS,
        birth_affect=AFFECT,
        ts_utc=TS,
        mid="c5b",
        source_ptrs=[],
        config=cfg,
    )
    assert r["verdict"] in ("accept", "suspect")
    assert r["crystal"]["fingerprint"] is None
    assert r["crystal"]["fp_error"]


def test_smelt_rejects_empty_source_material():
    r = smelt(
        "a plausible but unsupported memory",
        [],
        birth_affect=AFFECT,
        ts_utc=TS,
        mid="empty",
        source_ptrs=[],
    )
    assert r["verdict"] == "reject"
    assert r["fails"] == ["sources"]
    assert r["crystal"] is None


def test_born_heat_clamps_bad_intensities_to_valid_range():
    assert born_heat([{"intensity": -5.0}]) >= 0.0
    assert born_heat([{"intensity": 9.0}]) <= 0.9


def test_smelted_crystal_is_drillable_end_to_end():
    # the pipeline joins up: smelt seals a fingerprint whose src names the source
    # memory, and drill walks it back down to the original material
    src_leaf = make_fingerprint([PTRS[0]], AFFECT, TS, mid="m1")
    r = smelt(
        GOOD, ROWS, birth_affect=AFFECT, ts_utc=TS, mid="c6", source_ptrs=PTRS, src_ids=["m1"]
    )
    tree = drill(r["crystal"]["fingerprint"], {"m1": src_leaf}.get)
    assert tree["mid"] == "c6"
    assert [s["mid"] for s in tree["sources"]] == ["m1"]
    assert leaf_pointers(tree)  # reaches the original transcript window


def test_unsealed_fallback_still_rejects_invalid_birth_metadata():
    cfg = SmeltConfig(require_fingerprint=False)
    invalid_cases = [
        {"mid": "", "birth_affect": AFFECT, "ts_utc": TS},
        {"mid": "x", "birth_affect": {"valence": float("nan")}, "ts_utc": TS},
        {"mid": "x", "birth_affect": AFFECT, "ts_utc": "2026-07-02T09:00:00"},
    ]
    for case in invalid_cases:
        r = smelt(
            GOOD,
            ROWS,
            mid=case["mid"],
            birth_affect=case["birth_affect"],
            ts_utc=case["ts_utc"],
            source_ptrs=[],
            config=cfg,
        )
        assert r["verdict"] == "reject"
        assert r["crystal"] is None
        assert "birth-metadata" in r["fails"]
