"""feltstate.affect.relationship — how the bond with the user evolves over time.

The :class:`~feltstate.state.Relationship` dimensions — closeness, trust, safety,
unresolved tension, and repair history — are not static. A warm stretch slowly
draws the agent closer and earns trust; friction raises tension and chips at felt
safety; an appraised act of care or betrayal moves things faster; and *repair*
(making up after a rupture) accumulates as trust capital that never decays — "we
have fought and come back before, so a rough patch is survivable."

:func:`update_relationship` integrates one turn's reading into a new
:class:`Relationship`. Everything is slow and bounded (see
:class:`~feltstate.config.RelationshipConfig`): a bond is built over many turns,
not declared in one. The function is pure — it returns a new object and never
mutates its input.

Like the rest of feltstate, this reacts to *measured* signal — the turn's valence
and its appraised ``milestones`` — not to anything the reply model asserts about
the relationship.
"""

from __future__ import annotations

from ..config import RelationshipConfig
from ..state import AffectDelta, Relationship

# Milestone-kind substrings grouped by how they move the bond. Matched
# case-insensitively so callers can namespace kinds freely (e.g. "warmth_care").
_CARE = ("care", "warmth", "love")
_GRATITUDE = ("gratitude", "thanks")
_SECURE = ("secure", "reassurance", "safety", "kept_promise")
_BETRAYAL = ("betrayal", "deception", "abandonment")
_CONFLICT = ("conflict",)
_REPAIR = ("repair",)
_REJECTION = ("rejection", "boundary", "broken_promise", "disappointment")


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _any(kind: str, subs: tuple[str, ...]) -> bool:
    return any(s in kind for s in subs)


def update_relationship(
    rel: Relationship, delta: AffectDelta, cfg: RelationshipConfig
) -> Relationship:
    """Integrate one turn into a new :class:`Relationship`.

    The update, in order:

    1. **Ordinary drift** from the turn's tone — a clearly warm turn nudges
       closeness / trust / safety up; a clearly cold one nudges closeness and
       safety down. Small, so a single sour exchange does not undo a bond.
    2. **Tension eases** by ``cfg.tension_decay`` every tick — friction fades if
       nothing keeps feeding it.
    3. **Milestone shifts** — appraised events move things faster and more
       specifically: care/warmth earn trust and closeness; betrayal costs trust
       and spikes tension; conflict raises tension; **repair** banks trust
       capital (``repair_history``, which only ever grows) and clears tension.

    closeness/trust/safety are clamped to ``[clamp_lo, clamp_hi]``;
    ``unresolved_tension`` to ``[0, 1]``; ``repair_history`` only accumulates.
    """
    closeness = float(rel.closeness)
    trust = float(rel.trust)
    safety = float(rel.safety)
    tension = float(rel.unresolved_tension)
    repair = float(rel.repair_history)

    # 1. Ordinary drift from the turn's tone.
    v = float(delta.valence)
    if v > 0.2:
        closeness += cfg.closeness_up * v
        trust += cfg.trust_up * v
        safety += cfg.safety_up * v
    elif v < -0.2:
        closeness -= cfg.closeness_down * abs(v)
        safety -= cfg.safety_down * abs(v)

    # 2. Tension eases on its own.
    tension = max(0.0, tension - cfg.tension_decay)

    # 3. Milestone-driven shifts (scaled by severity).
    for m in delta.milestones or []:
        if not isinstance(m, dict):
            continue
        kind = str(m.get("kind", "")).lower()
        sev = max(0.0, min(1.0, float(m.get("severity", 0.5))))

        if _any(kind, _CARE):
            trust += cfg.trust_per_care * sev
            closeness += cfg.closeness_per_warmth * sev
            safety += cfg.safety_up * sev
        elif _any(kind, _GRATITUDE):
            closeness += cfg.closeness_per_warmth * 0.5 * sev
        elif _any(kind, _SECURE):
            safety += cfg.safety_up * 2.0 * sev
            trust += cfg.trust_per_care * 0.5 * sev
        elif _any(kind, _BETRAYAL):
            trust -= cfg.trust_per_betrayal * sev
            tension += cfg.tension_per_conflict * sev
            safety -= cfg.safety_down * 2.0 * sev
        elif _any(kind, _REPAIR):
            repair += cfg.repair_per_event * sev
            tension = max(0.0, tension - cfg.tension_per_conflict * sev)
        elif _any(kind, _CONFLICT):
            tension += cfg.tension_per_conflict * sev
        elif _any(kind, _REJECTION):
            tension += cfg.tension_per_conflict * 0.6 * sev

    return Relationship(
        closeness=_clamp(closeness, cfg.clamp_lo, cfg.clamp_hi),
        trust=_clamp(trust, cfg.clamp_lo, cfg.clamp_hi),
        safety=_clamp(safety, cfg.clamp_lo, cfg.clamp_hi),
        unresolved_tension=_clamp(tension, 0.0, 1.0),
        repair_history=max(0.0, repair),
    )
