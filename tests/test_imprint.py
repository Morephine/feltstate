"""Imprint — permanent symmetric marks: ingest, one-time trait shift (idempotent),
slow floored decay, throttled echo, and the symmetry that keeps an agent from
drifting cold."""

from datetime import datetime, timedelta

from feltstate.affect.imprint import (
    Imprint,
    apply_trait_shift,
    baseline_from_imprints,
    check_echo,
    decay_imprints,
    echo_mood_nudge,
    ingest_milestones,
)
from feltstate.affect.traits import update_traits
from feltstate.config import DEFAULT_CONFIG
from feltstate.state import AffectDelta, Mood, Traits

TCFG = DEFAULT_CONFIG.traits

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
    assert check_echo([with_kw], "", _plus(T0, days=1)) == []  # empty text
    assert check_echo([with_kw], "coffee please", _plus(T0, days=1)) == []  # no match


# --- echo -> felt mood (a fired echo has a real, bounded effect) ---------- #
def test_echo_mood_nudge_pushes_in_the_imprints_direction():
    # A fired echo must actually move the felt mood — an old hurt stings afresh,
    # an old kindness warms afresh — in the imprint's own valence direction, and
    # (a stirred memory being activating) lift arousal a touch.
    base = Mood(valence=0.0, arousal=0.4)
    neg = echo_mood_nudge([Imprint(valence_sign=-1, intensity=1.0)], base)
    pos = echo_mood_nudge([Imprint(valence_sign=+1, intensity=1.0)], base)
    assert neg.valence < 0.0 and pos.valence > 0.0  # opposite signs pull opposite ways
    assert neg.arousal > base.arousal and pos.arousal > base.arousal
    # Symmetric magnitude: a warm memory colours as strongly as a sore one.
    assert round(pos.valence, 4) == round(-neg.valence, 4)
    # Nothing fired -> the mood is returned untouched (same object; a true no-op).
    assert echo_mood_nudge([], base) is base


def test_echo_mood_nudge_scales_with_intensity():
    # A faded scar (low intensity) stirs the mood less than a vivid one.
    base = Mood(valence=0.0, arousal=0.4)
    faint = echo_mood_nudge([Imprint(valence_sign=-1, intensity=0.2)], base)
    vivid = echo_mood_nudge([Imprint(valence_sign=-1, intensity=1.0)], base)
    assert abs(faint.valence) < abs(vivid.valence)


def test_echo_mood_nudge_is_bounded_under_repeated_echoes():
    # The whole safety property: repeated echoes (even 1000 in one call) converge
    # toward a sub-unit target and can NEVER drive the mood past the [-1, 1] rail.
    base = Mood(valence=0.0, arousal=0.4)
    hammered = echo_mood_nudge([Imprint(valence_sign=-1, intensity=1.0)] * 1000, base)
    assert -1.0 <= hammered.valence < 0.0  # strictly inside the negative rail
    assert hammered.valence > -0.90  # asymptotes at the 0.85 target, never runs away
    assert 0.0 <= hammered.arousal <= 1.0


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


# --- persistent baseline: the imprint's lift does NOT wash out (findings #4/#5) #
def _idle(traits: Traits, *, n: int) -> Traits:
    """Run ``n`` idle (no-signal) ticks — baseline pull only."""
    empty = AffectDelta(labels=[])
    for _ in range(n):
        traits = update_traits(traits, empty, TCFG)
    return traits


def test_baseline_from_imprints_offsets_the_resting_point():
    # A warmth imprint moves the resting point of the traits it touches; a trait
    # no imprint touched is omitted (it rests at the neutral 0.5 by default).
    imp = ingest_milestones([{"kind": "warmth", "severity": 1.0}], T0)[0]
    base = baseline_from_imprints([imp])
    assert base["optimism"] > 0.5 and base["depression"] < 0.5
    assert "anxiety" not in base  # warmth's shift table doesn't touch anxiety
    # Empty in -> empty out (a fresh temperament rests everywhere at neutral).
    assert baseline_from_imprints([]) == {}


def test_imprint_lift_persists_over_many_idle_ticks():
    """The regression: an imprint's trait lift is still clearly present after a
    long idle stretch, instead of decaying back to the 0.5 baseline. Optimism is
    the fastest-relaxing trait, so if the lift survives here it survives anywhere.
    """
    imp = ingest_milestones([{"kind": "warmth", "severity": 1.0}], T0)[0]
    traits = apply_trait_shift(Traits(), imp)  # immediate felt jump
    lifted = traits.optimism
    assert lifted > 0.5
    # The permanent resting-point offset is what makes it durable.
    traits.baseline = baseline_from_imprints([imp])

    # Idle for far longer than it took the OLD behaviour to evaporate (~200 ticks).
    settled = _idle(traits, n=500)
    # Still clearly lifted — within a whisker of the shifted resting point, and
    # nowhere near neutral. (Old behaviour: optimism back to ~0.50 by ~200 ticks.)
    assert abs(settled.optimism - traits.baseline["optimism"]) < 0.01
    assert settled.optimism > 0.54  # essentially the full lift, not decayed away


def test_without_baseline_the_lift_still_decays_documents_the_bug():
    # Guard the contract: the one-time jump ALONE (no baseline offset) is exactly
    # the old, buggy behaviour — it washes out over idle ticks. This is why the
    # persistent baseline is required, not optional.
    imp = ingest_milestones([{"kind": "warmth", "severity": 1.0}], T0)[0]
    traits = apply_trait_shift(Traits(), imp)  # jump only, baseline left neutral
    assert traits.baseline == {}
    settled = _idle(traits, n=300)
    assert abs(settled.optimism - 0.5) < 0.005  # decayed all the way back to neutral


def test_idle_asymmetry_preserved_when_no_imprint():
    # The persistent-baseline change must not disturb the plain asymmetry: with no
    # imprint, optimism still relaxes toward neutral faster than depression does.
    t = Traits(optimism=0.8, depression=0.8)  # both elevated, no baseline offset
    t = _idle(t, n=40)
    assert (0.8 - t.optimism) > (0.8 - t.depression)  # optimism shed more
    assert t.optimism < t.depression  # and sits closer to neutral


def test_stable_id_dedups_the_same_milestone_across_ticks():
    # Finding #8: the same semantic milestone on a LATER tick must share the id of
    # the earlier one, so an engine keying on id recognises it and does not stack a
    # second imprint (which is what bounds runaway stacking, finding #9).
    early = ingest_milestones([{"kind": "warmth", "label": "kind word", "severity": 0.8}], T0)[0]
    later = ingest_milestones(
        [{"kind": "warmth", "label": "kind word", "severity": 0.8}], _plus(T0, days=7)
    )[0]
    assert early.id == later.id  # timestamp is NOT part of the key
    # A genuinely different event (different label) gets a different id.
    other = ingest_milestones([{"kind": "warmth", "label": "a hug", "severity": 0.8}], T0)[0]
    assert other.id != early.id


def test_bounded_stacking_dedup_keeps_the_baseline_from_running_away():
    # Finding #9: with the stable id, a source re-emitting the SAME milestone every
    # tick collapses to one imprint, so the derived resting point equals that of a
    # single imprint — it does not ratchet unboundedly toward the extreme.
    ids: dict[str, Imprint] = {}
    for day in range(50):  # same milestone, 50 different ticks
        imp = ingest_milestones(
            [{"kind": "warmth", "label": "kind word", "severity": 1.0}], _plus(T0, days=day)
        )[0]
        ids.setdefault(imp.id, imp)  # dedup exactly as the engine does, by id
    kept = list(ids.values())
    assert len(kept) == 1  # 50 emissions collapsed to one imprint
    one = baseline_from_imprints(kept)
    solo = baseline_from_imprints(ingest_milestones([{"kind": "warmth", "severity": 1.0}], T0))
    assert one == solo  # no unbounded accumulation
    assert one["optimism"] <= 0.95  # and always inside the clamp


# --- serialization -------------------------------------------------------- #
def test_round_trip():
    imp = ingest_milestones(
        [{"kind": "care", "severity": 0.8, "echo_keywords": ["x"], "label": "tag"}], T0
    )[0]
    imp.shifts_applied = True
    restored = Imprint.from_dict(imp.to_dict())
    assert restored.to_dict() == imp.to_dict()
