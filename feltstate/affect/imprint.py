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

from ..state import Traits

# --------------------------------------------------------------------------- #
# Tunables (intentionally local — these are imprint dynamics, not global cfg). #
# --------------------------------------------------------------------------- #
DEFAULT_DECAY_PER_DAY = 0.001   # ~years to fade from 1.0 toward the floor
DEFAULT_MIN_FLOOR = 0.15        # an imprint can scar over but never disappear
DEFAULT_ECHO_THROTTLE_H = 4.0   # surface a given imprint at most this often
ECHO_INTENSITY_BUMP = 0.05      # a touched subject flares back to vividness
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
    "care":      (+1, {"optimism": +0.05, "depression": -0.04, "curiosity": +0.03}),
    "warmth":    (+1, {"optimism": +0.05, "depression": -0.04, "curiosity": +0.03}),
    "love":      (+1, {"optimism": +0.05, "depression": -0.04, "curiosity": +0.03}),
    # thanks / being valued — a quieter lift
    "gratitude": (+1, {"optimism": +0.04, "depression": -0.03}),
    "thanks":    (+1, {"optimism": +0.04, "depression": -0.03}),
    # felt safety / trust — mainly calms anxiety
    "secure":    (+1, {"anxiety": -0.05, "depression": -0.03}),
    "safety":    (+1, {"anxiety": -0.05, "depression": -0.03}),
    "trust":     (+1, {"anxiety": -0.04, "optimism": +0.03}),
    # a promise kept — restores faith
    "kept_promise": (+1, {"optimism": +0.04, "anxiety": -0.02}),
}
_NEGATIVE_KINDS: dict[str, tuple[int, dict[str, float]]] = {
    # being deceived / let down by someone trusted — wariness, lost faith
    "betrayal":       (-1, {"optimism": -0.06, "anxiety": +0.04}),
    "deception":      (-1, {"optimism": -0.06, "anxiety": +0.04}),
    # a loss — settles into the low mood
    "loss":           (-1, {"depression": +0.05, "optimism": -0.03}),
    "grief":          (-1, {"depression": +0.05, "optimism": -0.03}),
    # a broken promise / being let down — dims optimism, withdraws a little
    # curiosity ("next time I won't reach out as far").
    "disappointment": (-1, {"optimism": -0.05, "anxiety": +0.02, "curiosity": -0.02}),
    "broken_promise": (-1, {"optimism": -0.05, "anxiety": +0.02, "curiosity": -0.02}),
    "abandonment":    (-1, {"depression": +0.04, "anxiety": +0.04, "optimism": -0.03}),
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


def _hash_id(kind: str, label: str, ts: str) -> str:
    h = hashlib.sha1(f"{kind}|{label}|{ts}".encode("utf-8")).hexdigest()[:8]
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
        Stable identifier derived from ``(kind, label, ts)``; used to dedup so
        the same event ingested twice does not stack.
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
    valence_sign: int = 0           # +1 positive, -1 negative
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
    label: str = ""                 # short human tag, for rendering / dedup

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
    def from_dict(cls, d: dict) -> "Imprint":
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
        ``(kind, label, ts)``.
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
                id=_hash_id(kind, label, ts),
                ts=ts,
                kind=kind,
                valence_sign=sign,
                severity=severity,
                intensity=severity,          # starts as vivid as it was deep
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
    """Apply an imprint's one-time, permanent nudge to long-term traits.

    Returns a **new** :class:`Traits` with the imprint's ``trait_shifts`` added,
    each result clamped to ``[0.05, 0.95]`` so a single imprint can never pin a
    trait to its extreme (room is always left for later signals, including
    opposite-signed ones — that headroom is what lets warmth offset trauma over
    time).

    Idempotent guard: if ``imp.shifts_applied`` is already true, the traits are
    returned unchanged. On a fresh apply the flag is set on ``imp`` so a tick
    loop can call this for every imprint without double-counting.

    Unlike mood or pressure, this shift does **not** decay — it permanently
    moves the resting baseline the rest of the system relaxes toward. That is
    what makes the event leave a lasting mark on temperament.
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
