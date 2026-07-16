"""feltstate.affect.imprint — permanent emotional imprints (optional enhancement).

Most of the felt state decays back to neutral: moods cool (``affect.traits``),
pressure bars settle to a floor (``affect.pressure``). That is correct for the
*texture* of feeling, but a person is also shaped by a handful of events that
**do not wash out** — being deeply cared for, being betrayed, a loss. Those
leave a mark that lasts for months and colours the standing temperament for
good. This module models that residue.

An :class:`Imprint` is a single such event. It carries:

* a one-time **trait shift** applied once at ingest (a profound disappointment
  nudges optimism down for good; sustained warmth nudges it up),
* an **intensity** that starts high and decays *extremely* slowly (~0.001/day,
  i.e. years to fade), but never below a per-imprint ``min_floor`` — it can
  scar over, never vanish,
* a set of **echo keywords**: when the user later touches the same subject, the
  imprint flares back to vividness (``check_echo``), throttled so it surfaces at
  most once every few hours instead of every turn.

**Why both signs — the symmetry rule.** Negative imprints (trauma, betrayal,
loss) and positive ones (warmth, care, gratitude, felt safety) are deliberately
treated identically: same slow decay, same floor, same one-time trait shift,
same echo mechanic. They differ only in ``valence_sign`` and in *which* traits
they move. This symmetry is the whole point. A system that remembers only the
wounds — only what hurt — will drift colder and warier with every hard
conversation, because nothing ever offsets the accumulating negative shifts. To
stay believable an agent must also keep a permanent record of having been loved
and trusted. Positive and negative both leave a mark, or the character slowly
goes cold.

**Generality.** Nothing here knows about any specific character, relationship,
or language. Imprints are created from *milestones* — the discrete appraised
events an :class:`~feltstate.state.AffectDelta` already carries — and the echo
keywords are whatever the milestone supplied (or none). There is no built-in
phrase list, no content corpus; the appraisal of "this was a betrayal" /
"this was warmth" happens upstream in whatever
:class:`~feltstate.sources.base.AffectSource` produced the milestone.

This module is a self-contained, optional layer: an :class:`~feltstate.engine.Engine`
may keep a list of imprints alongside the :class:`~feltstate.state.AffectState`,
feed each tick's ``delta.milestones`` through :func:`ingest_milestones`, apply
the resulting shifts once via :func:`apply_trait_shift`, age them with
:func:`decay_imprints`, and surface flares with :func:`check_echo`. Skipping it
entirely just means the agent has a shorter memory.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..state import Mood, Traits

# --------------------------------------------------------------------------- #
# Tunables (intentionally local — these are imprint dynamics, not global cfg). #
# --------------------------------------------------------------------------- #
DEFAULT_DECAY_PER_DAY = 0.001  # ~years to fade from 1.0 toward the floor
DEFAULT_MIN_FLOOR = 0.15  # an imprint can scar over but never disappear
DEFAULT_ECHO_THROTTLE_H = 4.0  # surface a given imprint at most this often
ECHO_INTENSITY_BUMP = 0.05  # a touched subject flares back to vividness
# How hard a fired echo colours the *current felt mood* (see ``echo_mood_nudge``).
# The valence push is a fraction of the remaining headroom toward a signed target,
# so it is proportional to the imprint's intensity yet asymptotes — many repeated
# echoes converge, they never blow the mood past the cap. Small on purpose: an old
# feeling stirring is a colouring, not a takeover.
ECHO_MOOD_GAIN = 0.25  # fraction of the headroom-to-target closed per fired echo
ECHO_MOOD_TARGET = 0.85  # |valence| an echo pulls toward at full intensity (< 1)
ECHO_AROUSAL_GAIN = 0.06  # a stirred memory lifts arousal slightly, per echo
# Trait clamp — kept loose so one imprint never pins a trait to the extreme;
# many small shifts can stack, but a single signal always leaves headroom.
_TRAIT_CLAMP_LO = 0.05
_TRAIT_CLAMP_HI = 0.95
_TRAIT_NAMES = ("depression", "optimism", "anxiety", "curiosity")


# --------------------------------------------------------------------------- #
# Kind taxonomy — maps a milestone "kind" onto sign + default trait shifts.   #
# --------------------------------------------------------------------------- #
# A milestone whose ``kind`` contains one of these substrings is recognised as
# a deep event worth imprinting. The shifts are *base* magnitudes for a
# full-severity event; ingest scales them by the milestone's severity so a mild
# event imprints proportionally less. Symmetric on purpose: the positive table
# mirrors the negative one so good events can offset bad ones over a lifetime.
#
# Each entry: substring -> (valence_sign, {trait: base_shift}).
_POSITIVE_KINDS: dict[str, tuple[int, dict[str, float]]] = {
    # being cared for / loved — lifts optimism, eases the low mood
    "care": (+1, {"optimism": +0.05, "depression": -0.04, "curiosity": +0.03}),
    "warmth": (+1, {"optimism": +0.05, "depression": -0.04, "curiosity": +0.03}),
    "love": (+1, {"optimism": +0.05, "depression": -0.04, "curiosity": +0.03}),
    # thanks / being valued — a quieter lift
    "gratitude": (+1, {"optimism": +0.04, "depression": -0.03}),
    "thanks": (+1, {"optimism": +0.04, "depression": -0.03}),
    # felt safety / trust — mainly calms anxiety
    "secure": (+1, {"anxiety": -0.05, "depression": -0.03}),
    "safety": (+1, {"anxiety": -0.05, "depression": -0.03}),
    "trust": (+1, {"anxiety": -0.04, "optimism": +0.03}),
    # a promise kept — restores faith
    "kept_promise": (+1, {"optimism": +0.04, "anxiety": -0.02}),
}
_NEGATIVE_KINDS: dict[str, tuple[int, dict[str, float]]] = {
    # being deceived / let down by someone trusted — wariness, lost faith
    "betrayal": (-1, {"optimism": -0.06, "anxiety": +0.04}),
    "deception": (-1, {"optimism": -0.06, "anxiety": +0.04}),
    # a loss — settles into the low mood
    "loss": (-1, {"depression": +0.05, "optimism": -0.03}),
    "grief": (-1, {"depression": +0.05, "optimism": -0.03}),
    # a broken promise / being let down — dims optimism, withdraws a little
    # curiosity ("next time I won't reach out as far").
    "disappointment": (-1, {"optimism": -0.05, "anxiety": +0.02, "curiosity": -0.02}),
    "broken_promise": (-1, {"optimism": -0.05, "anxiety": +0.02, "curiosity": -0.02}),
    "abandonment": (-1, {"depression": +0.04, "anxiety": +0.04, "optimism": -0.03}),
}


def _classify(kind: str) -> tuple[int, dict[str, float]] | None:
    """Match a milestone ``kind`` against the imprint taxonomy.

    Returns ``(valence_sign, base_trait_shifts)`` or ``None`` if the kind is not
    a deep event (ordinary milestones do not imprint). Matching is by substring
    so callers can namespace kinds freely, e.g. ``"warmth_love"`` or
    ``"trauma_betrayal"``. Negative substrings are checked first so a kind that
    mentions both (rare) errs toward caution.
    """
    k = (kind or "").lower()
    for sub, spec in _NEGATIVE_KINDS.items():
        if sub in k:
            return spec
    for sub, spec in _POSITIVE_KINDS.items():
        if sub in k:
            return spec
    return None


# --------------------------------------------------------------------------- #
# Time helpers (timezone-aware; tolerant of trailing "Z").                    #
# --------------------------------------------------------------------------- #
def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _days_between(earlier: datetime | None, later: datetime | None) -> float:
    if earlier is None or later is None:
        return 0.0
    return max(0.0, (later - earlier).total_seconds() / 86400.0)


def _hash_id(kind: str, label: str) -> str:
    # 128-bit id (was 32-bit [:8], collision-prone at scale). The id is keyed on
    # ``(kind, label)`` ONLY — deliberately not on the timestamp — so the *same*
    # semantic milestone emitted on a later tick produces the *same* id and the
    # engine's id-based dedup recognises it as already imprinted (it does not
    # stack a second shift). Two genuinely distinct events must therefore differ
    # in kind or label; a source that wants every occurrence to imprint should
    # vary the label (e.g. include the date).
    h = hashlib.sha256(f"{kind}|{label}".encode()).hexdigest()[:32]
    return f"imprint_{h}"


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# --------------------------------------------------------------------------- #
# The imprint record                                                          #
# --------------------------------------------------------------------------- #
@dataclass
class Imprint:
    """One permanent emotional mark left by a deep event.

    Attributes
    ----------
    id
        Stable identifier derived from ``(kind, label)`` — timestamp-independent
        on purpose, so the same semantic milestone recurring on a later tick gets
        the same id and dedups instead of stacking a second shift.
    ts
        ISO-8601 timestamp of the event. Anchors decay.
    kind
        The milestone kind this came from (e.g. ``"warmth"``, ``"betrayal"``).
        Free-form; only its match in the taxonomy is meaningful.
    valence_sign
        ``+1`` for a positive imprint, ``-1`` for a negative one. The symmetry
        that keeps the agent from drifting cold lives in this field.
    severity
        How deep the event was, in ``[0, 1]``. Permanent — never changes.
    intensity
        How *vivid* the imprint is right now, in ``[0, 1]``. Starts at
        ``severity`` and decays toward ``min_floor``; an echo bumps it back up.
    decay_per_day
        Intensity lost per day. Deliberately tiny (~0.001) so imprints last.
    min_floor
        Intensity never falls below this. A scar, not an erasure.
    echo_keywords
        Subjects that, if the user raises them again, make this imprint flare
        (see :func:`check_echo`). Supplied by the milestone; may be empty.
    last_echo_ts
        When this imprint last flared, for throttling. ``None`` until it does.
    trait_shifts
        The one-time, severity-scaled nudge to long-term :class:`Traits`,
        applied exactly once via :func:`apply_trait_shift`. ``shifts_applied``
        guards against re-applying.
    """

    id: str = ""
    ts: str = ""
    kind: str = ""
    valence_sign: int = 0  # +1 positive, -1 negative
    severity: float = 0.5
    intensity: float = 0.5
    decay_per_day: float = DEFAULT_DECAY_PER_DAY
    min_floor: float = DEFAULT_MIN_FLOOR
    echo_keywords: list[str] = field(default_factory=list)
    last_echo_ts: str | None = None
    trait_shifts: dict = field(default_factory=dict)
    # internal bookkeeping (not part of the contract signature, but persisted)
    shifts_applied: bool = False
    echo_count: int = 0
    label: str = ""  # short human tag, for rendering / dedup

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ts": self.ts,
            "kind": self.kind,
            "valence_sign": int(self.valence_sign),
            "severity": round(self.severity, 4),
            "intensity": round(self.intensity, 4),
            "decay_per_day": self.decay_per_day,
            "min_floor": round(self.min_floor, 4),
            "echo_keywords": list(self.echo_keywords),
            "last_echo_ts": self.last_echo_ts,
            "trait_shifts": dict(self.trait_shifts),
            "shifts_applied": bool(self.shifts_applied),
            "echo_count": int(self.echo_count),
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Imprint:
        d = d or {}
        return cls(
            id=str(d.get("id", "") or ""),
            ts=str(d.get("ts", "") or ""),
            kind=str(d.get("kind", "") or ""),
            valence_sign=int(d.get("valence_sign", 0) or 0),
            severity=float(d.get("severity", 0.5)),
            intensity=float(d.get("intensity", d.get("severity", 0.5))),
            decay_per_day=float(d.get("decay_per_day", DEFAULT_DECAY_PER_DAY)),
            min_floor=float(d.get("min_floor", DEFAULT_MIN_FLOOR)),
            echo_keywords=list(d.get("echo_keywords") or []),
            last_echo_ts=d.get("last_echo_ts"),
            trait_shifts=dict(d.get("trait_shifts") or {}),
            shifts_applied=bool(d.get("shifts_applied", False)),
            echo_count=int(d.get("echo_count", 0) or 0),
            label=str(d.get("label", "") or ""),
        )


# --------------------------------------------------------------------------- #
# Ingest — turn appraised milestones into imprints                            #
# --------------------------------------------------------------------------- #
def ingest_milestones(milestones: list[dict], ts: str) -> list[Imprint]:
    """Create :class:`Imprint` records from this turn's appraised milestones.

    Only milestones whose ``kind`` matches the deep-event taxonomy (warmth /
    care / gratitude / secure / trust / kept_promise on the positive side;
    trauma / betrayal / loss / disappointment / abandonment on the negative
    side) become imprints — ordinary milestones are ignored.

    Each recognised milestone may carry:

    * ``kind`` (required) — routed through the taxonomy for sign + trait shifts;
    * ``severity`` — depth in ``[0, 1]`` (default 0.5); scales both the starting
      intensity and the trait shift, so a mild event imprints proportionally;
    * ``echo_keywords`` — optional list of subjects that later re-trigger this
      imprint (see :func:`check_echo`); empty if the milestone supplies none;
    * ``label`` — optional short human tag (used for dedup and rendering); falls
      back to the kind.

    Parameters
    ----------
    milestones
        ``delta.milestones`` for the turn — a list of plain dicts.
    ts
        ISO-8601 timestamp to stamp the new imprints with (the tick time).

    Returns
    -------
    list[Imprint]
        Newly created imprints (possibly empty). Trait shifts are stored but not
        yet applied; the caller applies them once via :func:`apply_trait_shift`.
        De-duplication against an existing imprint list is the caller's job — it
        can compare on :attr:`Imprint.id`, which is stable for a given
        ``(kind, label)`` regardless of tick, so a milestone that recurs across
        ticks dedups instead of stacking.
    """
    out: list[Imprint] = []
    for ms in milestones or []:
        if not isinstance(ms, dict):
            continue
        kind = str(ms.get("kind", "") or "")
        spec = _classify(kind)
        if spec is None:
            continue
        sign, base_shifts = spec
        severity = _clamp(float(ms.get("severity", 0.5)), 0.0, 1.0)
        label = str(ms.get("label", "") or kind)
        # Scale the one-time trait shift by severity so a mild event nudges less.
        shifts = {k: round(v * severity, 4) for k, v in base_shifts.items()}
        out.append(
            Imprint(
                id=_hash_id(kind, label),
                ts=ts,
                kind=kind,
                valence_sign=sign,
                severity=severity,
                intensity=severity,  # starts as vivid as it was deep
                decay_per_day=DEFAULT_DECAY_PER_DAY,
                # floor scales with depth: a deeper mark leaves a higher residue,
                # but never below the global minimum.
                min_floor=max(DEFAULT_MIN_FLOOR, severity * 0.2),
                echo_keywords=[str(k) for k in (ms.get("echo_keywords") or [])],
                last_echo_ts=None,
                trait_shifts=shifts,
                shifts_applied=False,
                label=label,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Apply the one-time permanent trait shift                                    #
# --------------------------------------------------------------------------- #
def apply_trait_shift(traits: Traits, imp: Imprint) -> Traits:
    """Apply an imprint's one-time nudge to the *current* trait values.

    Returns a **new** :class:`Traits` with the imprint's ``trait_shifts`` added
    to the current values, each result clamped to ``[0.05, 0.95]`` so a single
    imprint can never pin a trait to its extreme (room is always left for later
    signals, including opposite-signed ones — that headroom is what lets warmth
    offset trauma over time).

    This moves where the trait *is right now*. On its own that nudge would decay:
    :func:`~feltstate.affect.traits.update_traits` relaxes each trait toward its
    resting point every tick. **Durability comes from the resting point moving
    too** — see :func:`baseline_from_imprints`, which the engine folds into
    ``traits.baseline`` so idle ticks relax toward the shifted point, not back to
    neutral. This function supplies the immediate felt jump; the baseline supplies
    the lasting mark. Keeping them separate is what lets trimming an imprint undo
    its lasting effect (recompute the baseline from what's kept) without having to
    unwind an already-applied jump.

    Idempotent guard: if ``imp.shifts_applied`` is already true, the traits are
    returned unchanged. On a fresh apply the flag is set on ``imp`` so a tick
    loop can call this for every imprint without double-counting.

    The persistent ``baseline`` map on ``traits`` is carried through unchanged
    (this function only touches the current values).
    """
    if imp.shifts_applied or not imp.trait_shifts:
        return traits
    updated = Traits(**traits.to_dict())
    for name, amount in imp.trait_shifts.items():
        if name not in _TRAIT_NAMES:
            continue
        cur = float(getattr(updated, name))
        setattr(updated, name, _clamp(cur + float(amount), _TRAIT_CLAMP_LO, _TRAIT_CLAMP_HI))
    imp.shifts_applied = True
    return updated


def baseline_from_imprints(imprints: list[Imprint]) -> dict[str, float]:
    """Derive the persistent per-trait resting point from the kept imprints.

    Each imprint permanently displaces the resting point of the traits it
    touches. This sums every imprint's ``trait_shifts`` onto the neutral 0.5 and
    clamps to ``[0.05, 0.95]``, returning ``{trait: resting_point}`` for exactly
    the traits some imprint has moved (a trait no imprint touches is omitted, and
    :func:`~feltstate.affect.traits.update_traits` treats it as resting at the
    configured neutral baseline).

    Deriving the baseline from the *current* imprint list — rather than mutating
    it incrementally as imprints arrive — is what makes the layer self-correct:

    * **Trimming is safe** (finding #10). When :attr:`Engine.max_imprints` drops
      the least-vivid marks, recomputing from what remains automatically removes
      the trimmed imprints' contribution, so no orphaned permanent offset is left
      behind.
    * **Stacking is bounded** (finding #9). With timestamp-independent ids a
      recurring milestone dedups to one imprint, so it contributes once; and the
      per-trait clamp caps how far even many same-signed imprints can push the
      resting point, always leaving headroom for the opposite sign.

    Positive and negative imprints net against each other here (the symmetry
    rule): a lifetime of warmth genuinely offsets old wounds in the resting point,
    not just momentarily.
    """
    offset: dict[str, float] = {}
    for imp in imprints or []:
        for name, amount in (imp.trait_shifts or {}).items():
            if name not in _TRAIT_NAMES:
                continue
            offset[name] = offset.get(name, 0.0) + float(amount)
    return {
        name: round(_clamp(0.5 + amt, _TRAIT_CLAMP_LO, _TRAIT_CLAMP_HI), 4)
        for name, amt in offset.items()
    }


# --------------------------------------------------------------------------- #
# Decay — age every imprint a little                                          #
# --------------------------------------------------------------------------- #
def decay_imprints(imprints: list[Imprint], ts: str) -> list[Imprint]:
    """Age imprints toward their floor based on elapsed wall-clock time.

    For each imprint, intensity is reduced by ``decay_per_day`` for every day
    since its last activity — whichever is more recent of its creation ``ts`` or
    its ``last_echo_ts`` (an echo re-anchors the clock, so a frequently revisited
    event stays vivid). Intensity never drops below ``min_floor``.

    The decay rate is deliberately tiny: at the default ~0.001/day it takes
    roughly two to three years to fall from full vividness to the floor. These
    are the feelings that *don't* fade on the scale that moods do — the slowness
    is the feature.

    Mutates the imprints in place (intensity / nothing else) and also returns the
    list, so it composes either way.
    """
    now = _parse_iso(ts)
    for imp in imprints or []:
        anchor = _parse_iso(imp.last_echo_ts) or _parse_iso(imp.ts)
        days = _days_between(anchor, now)
        if days <= 0.0:
            continue
        rate = float(imp.decay_per_day or DEFAULT_DECAY_PER_DAY)
        floor = float(imp.min_floor)
        imp.intensity = round(max(floor, float(imp.intensity) - rate * days), 4)
    return imprints


# --------------------------------------------------------------------------- #
# Echo — a touched subject flares the imprint back to vividness               #
# --------------------------------------------------------------------------- #
def check_echo(
    imprints: list[Imprint],
    user_text: str,
    ts: str,
    throttle_hours: float = 4.0,
) -> list[Imprint]:
    """Re-trigger imprints whose subject the user just raised again.

    Scans ``user_text`` for each imprint's ``echo_keywords`` (case-insensitive
    substring match). A hit, if the imprint has not flared within the last
    ``throttle_hours``, bumps its intensity by :data:`ECHO_INTENSITY_BUMP`
    (capped at 1.0), stamps ``last_echo_ts``, and increments ``echo_count``.
    This is how an old wound can sting afresh — or an old kindness warm afresh —
    when the same topic comes up, without surfacing on every single turn.

    The throttle matters: people don't re-feel the same memory every time a word
    appears. Once every few hours keeps the echo meaningful instead of constant.

    Imprints with no ``echo_keywords`` never echo (the event is remembered, but
    nothing in conversation specifically re-evokes it).

    Parameters
    ----------
    imprints
        The current imprint list (mutated in place on a hit).
    user_text
        The latest user message text to scan. Empty text echoes nothing.
    ts
        ISO-8601 timestamp of this turn (the echo time).
    throttle_hours
        Minimum hours between successive echoes of the same imprint.

    Returns
    -------
    list[Imprint]
        The imprints that flared this turn (a subset of the input), in input
        order. Empty if nothing was re-triggered. Both positive and negative
        imprints can echo — a kind word can warm as readily as a sore one stings.
    """
    text = (user_text or "").lower()
    if not text:
        return []
    now = _parse_iso(ts)
    fired: list[Imprint] = []
    for imp in imprints or []:
        keywords = imp.echo_keywords or []
        if not keywords:
            continue
        last = _parse_iso(imp.last_echo_ts)
        if last is not None and now is not None:
            if (now - last).total_seconds() < throttle_hours * 3600.0:
                continue
        if not any(str(kw).lower() in text for kw in keywords if kw):
            continue
        imp.intensity = round(min(1.0, float(imp.intensity) + ECHO_INTENSITY_BUMP), 4)
        imp.last_echo_ts = ts
        imp.echo_count = int(imp.echo_count) + 1
        fired.append(imp)
    return fired


# --------------------------------------------------------------------------- #
# Echo -> mood: apply a bounded effect from a fired imprint                    #
# --------------------------------------------------------------------------- #
def echo_mood_nudge(fired: list[Imprint], mood: Mood) -> Mood:
    """Colour the current felt ``mood`` with any imprints that echoed this turn.

    :func:`check_echo` only re-vivifies an imprint's *intensity*. This function
    converts a fired echo into a small, **bounded** adjustment of the fast mood,
    which is then visible in the rendered block and decays through the normal
    tick dynamics.

    For each fired imprint the felt valence is pulled a fraction
    (:data:`ECHO_MOOD_GAIN`, scaled by the imprint's current ``intensity``) of the
    way toward a signed target ``valence_sign * ECHO_MOOD_TARGET``, and arousal is
    lifted slightly (a stirred memory is activating). Because each step closes a
    fraction of the *remaining* distance to a target strictly inside ``[-1, 1]``,
    the effect is proportional to intensity yet **asymptotic**: repeated echoes
    (even many in one turn, or turn after turn) converge toward the target and can
    never drive the mood past it, so the state cannot blow up. Opposite-signed
    echoes simply pull the other way — a warm memory can temper a sore one.

    This is a *state* colouring, never an instruction. Returns a new
    :class:`Mood`; the input is not mutated. With nothing fired it is a no-op
    (the same mood object is returned unchanged).
    """
    if not fired:
        return mood

    v = float(mood.valence)
    a = float(mood.arousal)
    for imp in fired:
        sign = 1.0 if int(imp.valence_sign) >= 0 else -1.0
        # Intensity scales *how much* of the step is taken this echo, so a faded
        # scar stirs less than a vivid one; clamp to [0,1] defensively.
        strength = _clamp(float(imp.intensity), 0.0, 1.0)
        target = sign * ECHO_MOOD_TARGET
        v += (target - v) * ECHO_MOOD_GAIN * strength  # close a fraction of the gap
        a += ECHO_AROUSAL_GAIN * strength

    # Reuse the mood's other fields verbatim; only the felt point moved.
    from dataclasses import replace

    return replace(
        mood,
        valence=_clamp(round(v, 4), -1.0, 1.0),
        arousal=_clamp(round(a, 4), 0.0, 1.0),
    )
