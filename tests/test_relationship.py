"""Relationship dynamics — the bond now evolves: warm/cold drift, milestone
shifts (care/betrayal/conflict/repair), tension decay, repair as trust capital."""

from feltstate.affect.relationship import update_relationship
from feltstate.config import DEFAULT_CONFIG
from feltstate.state import AffectDelta, Relationship

RCFG = DEFAULT_CONFIG.relationship


def _d(valence=0.0, milestones=None):
    return AffectDelta(valence=valence, milestones=milestones or [])


def test_warm_turn_raises_the_bond():
    rel = Relationship()
    out = update_relationship(rel, _d(valence=0.6), RCFG)
    assert out.closeness > rel.closeness
    assert out.trust > rel.trust
    assert out.safety > rel.safety


def test_cold_turn_lowers_closeness_and_safety():
    rel = Relationship()
    out = update_relationship(rel, _d(valence=-0.6), RCFG)
    assert out.closeness < rel.closeness
    assert out.safety < rel.safety


def test_care_milestone_earns_trust_and_closeness():
    out = update_relationship(
        Relationship(), _d(milestones=[{"kind": "warmth_care", "severity": 1.0}]), RCFG
    )
    assert out.trust > 0.5 and out.closeness > 0.5


def test_betrayal_costs_trust_and_spikes_tension():
    out = update_relationship(
        Relationship(trust=0.6),
        _d(milestones=[{"kind": "trauma_betrayal", "severity": 1.0}]),
        RCFG,
    )
    assert out.trust < 0.6
    assert out.unresolved_tension > 0.0


def test_conflict_raises_tension():
    out = update_relationship(
        Relationship(), _d(milestones=[{"kind": "conflict", "severity": 1.0}]), RCFG
    )
    assert out.unresolved_tension > 0.0


def test_repair_accumulates_capital_and_clears_tension():
    rel = Relationship(unresolved_tension=0.4)
    out = update_relationship(rel, _d(milestones=[{"kind": "repair", "severity": 1.0}]), RCFG)
    assert out.repair_history > rel.repair_history  # only ever grows
    assert out.unresolved_tension < 0.4  # making up clears the friction


def test_tension_eases_on_its_own():
    out = update_relationship(Relationship(unresolved_tension=0.5), _d(valence=0.0), RCFG)
    assert out.unresolved_tension < 0.5


def test_bounded_and_pure():
    rel = Relationship(closeness=0.94, trust=0.94, safety=0.94)
    out = update_relationship(rel, _d(valence=1.0), RCFG)
    assert out.closeness <= 0.95 and out.trust <= 0.95 and out.safety <= 0.95
    assert rel.closeness == 0.94  # input untouched (pure)
