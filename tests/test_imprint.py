"""Imprint — permanent symmetric marks: ingest, one-time trait shift (idempotent),
slow floored decay, throttled echo, and the symmetry that keeps an agent from
drifting cold."""
from datetime import datetime, timedelta

from feltstate.affect.imprint import (
    Imprint,
    apply_trait_shift,
    check_echo,
    decay_imprints,
    ingest_milestones,
)
from feltstate.state import Traits

T0 = "2026-01-01T00:00:00"


def _plus(iso: str, **kw) -> str:
    return (datetime.fromisoformat(iso) + timedelta(**kw)).isoformat()


# --- ingest / taxonomy ---------------------------------------------------- #
def test_ingest_recognises_positive_and_negative_kinds():
    pos = ingest_milestones([{"kind": "warmth_love", "severity": 1.0}], T0)
    neg = ingest_milestones([{"kind": "trauma_betrayal", "severity": 1.0}], T0)
    assert len(pos) == 1 and pos[0].valence_sign == +1
    assert len(neg) == 1 and neg[0].valence_sign == -1
    # positive lifts optimism; negative dims it
    assert pos[0].trait_shifts.get("optimism", 0) > 0
    assert neg[0].trait_shifts.get("optimism", 0) < 0


def test_ingest_ignores_ordinary_milestones():
    assert ingest_milestones([{"kind": "chitchat"}, {"kind": "question"}], T0) == []
    assert ingest_milestones([], T0) == []
    # non-dict entries are skipped, not fatal
    assert ingest_milestones(["nope", None], T0) == []


def test_severity_scales_shift_and_intensity():
    mild = ingest_milestones([{"kind": "care", "severity": 0.5}], T0)[0]
    deep = ingest_milestones([{"kind": "care", "severity": 1.0}], T0)[0]
    assert deep.trait_shifts["optimism"] == round(2 * mild.trait_shifts["optimism"], 4)
    assert deep.intensity == 1.0 and mild.intensity == 0.5


def test_id_is_stable_for_dedup():
    a = ingest_milestones([{"kind": "warmth", "label": "kind word", "severity": 0.8}], T0)[0]
    b = ingest_milestones([{"kind": "warmth", "label": "kind word", "severity": 0.8}], T0)[0]
    assert a.id == b.id  # same (kind,label,ts) -> same id, so the engine can dedup


# --- one-time trait shift ------------------------------------------------- #
def test_apply_trait_shift_is_one_time_and_idempotent():
    imp = ingest_milestones([{"kind": "care", "severity": 1.0}], T0)[0]
    base = Traits()  # all 0.5
    once = apply_trait_shift(base, imp)
    assert once.optimism == 0.55 and round(once.depression, 4) == 0.46
    assert imp.shifts_applied is True
    # second application is a no-op (guard prevents double-counting in a tick loop)
    twice = apply_trait_shift(once, imp)
    assert twice.optimism == once.optimism


def test_trait_shift_clamps_and_leaves_headroom():
    # An imprint can never pin a trait to the extreme — opposite signals must
    # always be able to move it back (this is what lets warmth offset trauma).
    imp = Imprint(trait_shifts={"optimism": +5.0})
    out = apply_trait_shift(Traits(optimism=0.9), imp)
    assert out.optimism <= 0.95


# --- decay ---------------------------------------------------------------- #
def test_decay_is_slow_and_floored():
    imp = ingest_milestones([{"kind": "loss", "severity": 1.0}], T0)[0]
    assert imp.intensity == 1.0 and imp.min_floor == 0.2  # max(0.15, 1.0*0.2)
    decay_imprints([imp], _plus(T0, days=100))
    assert imp.intensity == 0.9  # 1.0 - 0.001*100, nowhere near gone
    decay_imprints([imp], _plus(T0, days=10000))
    assert imp.intensity == imp.min_floor  # scarred over, never vanished


# --- echo ----------------------------------------------------------------- #
def test_echo_fires_on_keyword_then_throttles():
    imp = ingest_milestones(
        [{"kind": "betrayal", "severity": 0.6, "echo_keywords": ["the deadline"]}], T0
    )[0]
    start = imp.intensity

    fired = check_echo([imp], "what happened with the deadline again?", _plus(T0, days=1))
    assert fired == [imp]
    assert imp.intensity > start and imp.echo_count == 1

    # within the throttle window -> no re-fire
    again = check_echo([imp], "the deadline still bugs me", _plus(T0, days=1, hours=1))
    assert again == []
    assert imp.echo_count == 1

    # past the throttle window -> fires again
    later = check_echo([imp], "about the deadline", _plus(T0, days=1, hours=5))
    assert later == [imp] and imp.echo_count == 2


def test_echo_needs_keywords_and_text():
    no_kw = ingest_milestones([{"kind": "warmth", "severity": 0.7}], T0)[0]
    assert check_echo([no_kw], "anything at all", _plus(T0, days=1)) == []
    with_kw = ingest_milestones(
        [{"kind": "warmth", "severity": 0.7, "echo_keywords": ["tea"]}], T0
    )[0]
    assert check_echo([with_kw], "", _plus(T0, days=1)) == []          # empty text
    assert check_echo([with_kw], "coffee please", _plus(T0, days=1)) == []  # no match


# --- symmetry (the whole point) ------------------------------------------- #
def test_symmetry_positive_offsets_negative_over_a_lifetime():
    # A betrayal then, much later, sustained warmth: optimism dips then recovers,
    # rather than ratcheting permanently down. Without symmetric positive
    # imprints the agent would only ever drift colder.
    traits = Traits()
    wound = ingest_milestones([{"kind": "betrayal", "severity": 1.0}], T0)[0]
    traits = apply_trait_shift(traits, wound)
    after_wound = traits.optimism
    assert after_wound < 0.5

    balm = ingest_milestones([{"kind": "warmth", "severity": 1.0}], _plus(T0, days=30))[0]
    traits = apply_trait_shift(traits, balm)
    assert traits.optimism > after_wound  # warmth pulled it back up


# --- serialization -------------------------------------------------------- #
def test_round_trip():
    imp = ingest_milestones(
        [{"kind": "care", "severity": 0.8, "echo_keywords": ["x"], "label": "tag"}], T0
    )[0]
    imp.shifts_applied = True
    restored = Imprint.from_dict(imp.to_dict())
    assert restored.to_dict() == imp.to_dict()
