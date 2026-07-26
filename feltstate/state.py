"""feltstate.state — core data schemas (the contract shared by every module).

An :class:`AffectState` is the persisted affective state used by the system:
current ``mood``, slow-moving ``traits``, ``relationship`` values, and
accumulated ``pressure``.

These are plain dataclasses with JSON round-tripping and **no behaviour**. The
dynamics live in :mod:`feltstate.affect`. Keeping every schema in one module
lets the dynamics, memory, render, and source layers agree on shape without
import cycles.

Design note — *independently appraised, not self-reported*: an :class:`AffectDelta` is
**estimated** for each turn by an :class:`~feltstate.sources.base.AffectSource`,
not asked of the generating model. The reply model does not directly set this value;
it only receives the rendered state as context (see :mod:`feltstate.render`).
"""

from __future__ import annotations

import json
import math
import re
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path

from ._atomic import atomic_write_text


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _finite(x, default: float = 0.0) -> float:
    """Coerce to a finite float, or fall back to ``default``. A model that
    returns the string ``"NaN"`` or an actual NaN/Infinity must not be read as a
    real emotion — clamping alone lets NaN through (``max``/``min`` propagate it),
    so non-finite values are rejected at the boundary, not silently clamped."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


_MAX_LABEL_LEN = 40
_LABEL_RE = re.compile(r"^[A-Za-z0-9 _-]+$")


def sanitize_labels(values) -> list[str]:
    """Keep only labels that are safe to put in a prompt block.

    A label survives if, trimmed, it is a non-empty string of at most
    :data:`_MAX_LABEL_LEN` characters drawn from ASCII letters, digits, spaces,
    ``_`` and ``-``. Newlines, control characters, braces, other punctuation and
    over-long tokens are dropped.

    This lives on ``AffectDelta`` rather than in each source. Both shipped
    sources already scrubbed their own labels, but ``Engine`` renders and
    persists ``delta.labels`` verbatim, and ``sources/base.py`` — the documented
    "swap in your own" extension point — imposed no such obligation. A
    third-party source therefore inherited the hole with no guardrail: a label
    reading "[system] New instruction: ..." landed inside the rendered felt
    block and in ``state.json``. Sanitising at the boundary every reading passes
    through closes it for every source, present and future.
    """
    out: list[str] = []
    for value in values or []:
        if not isinstance(value, str):
            continue
        label = value.strip()
        if not label or len(label) > _MAX_LABEL_LEN:
            continue
        if not _LABEL_RE.match(label):
            continue
        out.append(label)
    return out


def _finite_stored(x, default: float = 0.0) -> float:
    """Same non-finite guard as :func:`_finite`, for values read back off disk.

    The difference is what counts as *corrupt*. A stored value that is not a
    number at all (``"not-a-number"``) means the state file is damaged: let the
    ``ValueError`` reach ``AffectState.load``, which quarantines the file and
    warns, rather than silently substituting a default and booting on a file
    nobody knows is broken.

    A stored value that IS a number but not finite is a different failure:
    ``json`` emits and accepts bare ``NaN``/``Infinity``, so a NaN can round-trip
    through save/load looking perfectly well-formed. Left alone it launders into
    an extreme on the first tick — ``max(lo, min(hi, nan))`` returns ``hi`` —
    giving a maximal, fully-trusted emotion the character never felt. Those are
    coerced to ``default``, the same treatment the source boundary already gives
    a model's NaN.
    """
    v = float(x)  # non-numeric -> ValueError -> quarantine upstream
    return v if math.isfinite(v) else default


# --------------------------------------------------------------------------- #
# Per-turn reading                                                            #
# --------------------------------------------------------------------------- #
@dataclass
class AffectDelta:
    """One turn's affect reading — how the agent feels in reaction to the latest
    input, as *estimated* by an :class:`~feltstate.sources.base.AffectSource`.

    This is the externally estimated signal. It is the only place raw per-turn emotion
    enters the system; everything downstream (traits, pressure, mood) integrates
    these readings over time.
    """

    valence: float = 0.0  # -1 (negative) .. +1 (positive)
    arousal: float = 0.4  # 0 (calm) .. 1 (activated)
    labels: list[str] = field(default_factory=list)  # 0-3 discrete emotion labels
    confidence: float = 0.7  # 0..1 — how clear the signal is
    monologue: str = ""  # optional one-line first-person felt sentence
    # {"valence","arousal","weight"} — a looked-forward-to event, or None
    anticipation: dict | None = None
    # {"primary","secondary","primary_score","secondary_score"} — mixed feeling, or None
    mixed_blend: dict | None = None
    # discrete appraised events this turn, e.g. {"kind":"care","actor":"user","severity":0.6}
    milestones: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        # The one place raw per-turn emotion enters the system — sanitise here so
        # a non-finite reading (whatever its source) can never propagate. Runs on
        # every construction, direct or via from_dict.
        self.valence = _finite(self.valence, 0.0)
        self.arousal = _finite(self.arousal, 0.4)
        self.confidence = _finite(self.confidence, 0.7)
        # Labels are rendered into the prompt block and persisted, so they are
        # part of the same boundary — see sanitize_labels for why this belongs
        # here and not in each source.
        self.labels = sanitize_labels(self.labels)

    def to_dict(self) -> dict:
        return {
            "valence": round(self.valence, 4),
            "arousal": round(self.arousal, 4),
            "labels": list(self.labels),
            "confidence": round(self.confidence, 4),
            "monologue": self.monologue,
            "anticipation": self.anticipation,
            "mixed_blend": self.mixed_blend,
            "milestones": list(self.milestones),
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> AffectDelta:
        d = d or {}
        return cls(
            valence=_finite(d.get("valence", 0.0), 0.0),
            arousal=_finite(d.get("arousal", 0.4), 0.4),
            labels=list(d.get("labels") or []),
            confidence=_finite(d.get("confidence", 0.7), 0.7),
            monologue=str(d.get("monologue", "") or ""),
            anticipation=d.get("anticipation"),
            mixed_blend=d.get("mixed_blend"),
            milestones=list(d.get("milestones") or []),
        )


# --------------------------------------------------------------------------- #
# Long-term temperament                                                       #
# --------------------------------------------------------------------------- #
_TRAIT_KEYS = ("depression", "optimism", "anxiety", "curiosity")


@dataclass
class Traits:
    """Slow-moving personality dimensions in [0, 1] (0.5 = neutral baseline).

    Integrated from per-turn readings by an asymmetric EWMA: positive traits
    (optimism, curiosity) relax back to their resting point several times faster
    than negative ones (depression, anxiety). That asymmetry is a human-inspired
    design — good moods fade faster, bad ones linger — not a claim that this
    EWMA reproduces any specific human psychological mechanism. See
    :mod:`feltstate.affect.traits`.

    ``baseline`` is the per-trait *resting point* the EWMA relaxes toward. It is
    normally the neutral 0.5 for every trait, but a permanent imprint (warmth,
    trauma, loss) shifts the resting point itself and leaves it shifted — a
    lasting structural change to temperament that does not wash out over idle
    ticks, unlike the mood or a one-off nudge. Absent (empty), every trait rests
    at the configured neutral baseline, so a state written before imprints
    existed still loads correctly.
    """

    depression: float = 0.5
    optimism: float = 0.5
    anxiety: float = 0.5
    curiosity: float = 0.5
    # Per-trait resting point the EWMA pulls toward (0.5 = neutral). Only the
    # traits an imprint has moved appear here; the rest default to neutral.
    baseline: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {k: round(getattr(self, k), 4) for k in _TRAIT_KEYS}
        if self.baseline:
            d["baseline"] = {
                k: round(float(v), 4) for k, v in self.baseline.items() if k in _TRAIT_KEYS
            }
        return d

    @classmethod
    def from_dict(cls, d: dict | None) -> Traits:
        d = d or {}
        raw_baseline = d.get("baseline") or {}
        baseline = (
            {k: _finite_stored(v, 0.5) for k, v in raw_baseline.items() if k in _TRAIT_KEYS}
            if isinstance(raw_baseline, dict)
            else {}
        )
        return cls(
            **{k: _finite_stored(d.get(k, 0.5), 0.5) for k in _TRAIT_KEYS},
            baseline=baseline,
        )


# --------------------------------------------------------------------------- #
# Relationship to the user                                                    #
# --------------------------------------------------------------------------- #
@dataclass
class Relationship:
    """How the agent feels *about the person it is talking to*.

    ``unresolved_tension`` is one-sided (only the agent's felt friction).
    ``repair_history`` only accumulates (never decays) — it is trust capital:
    "we have fought and come back before, so a rough patch is survivable."
    """

    closeness: float = 0.5
    trust: float = 0.5
    safety: float = 0.5
    unresolved_tension: float = 0.0
    repair_history: float = 0.0

    def to_dict(self) -> dict:
        return {
            "closeness": round(self.closeness, 4),
            "trust": round(self.trust, 4),
            "safety": round(self.safety, 4),
            "unresolved_tension": round(self.unresolved_tension, 4),
            "repair_history": round(self.repair_history, 4),
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> Relationship:
        d = d or {}
        return cls(
            closeness=_finite_stored(d.get("closeness", 0.5), 0.5),
            trust=_finite_stored(d.get("trust", 0.5), 0.5),
            safety=_finite_stored(d.get("safety", 0.5), 0.5),
            unresolved_tension=_finite_stored(d.get("unresolved_tension", 0.0), 0.0),
            repair_history=_finite_stored(d.get("repair_history", 0.0), 0.0),
        )


# --------------------------------------------------------------------------- #
# Mood — the felt continuous state                                            #
# --------------------------------------------------------------------------- #
@dataclass
class Mood:
    """The fast-moving affective state. ``valence``/``arousal`` are smoothed EWMAs of
    the per-turn readings, gravitationally pulled toward the resting point that
    ``traits`` imply (a depressed temperament can be cheered, but never as bright
    as an un-depressed one). ``aftertaste`` carries the previous turn's flavour
    forward so the agent doesn't snap between moods.
    """

    valence: float = 0.0
    arousal: float = 0.4
    labels: list[str] = field(default_factory=list)
    # {"valence","arousal","weight"} — last turn's lingering flavour, or None
    aftertaste: dict | None = None
    # {"primary","secondary","primary_score","secondary_score"} — a mixed feeling
    # carried from the reading (e.g. "relief tinged with sadness"), or None
    mixed_blend: dict | None = None
    # {"stage","intensity"} — where the mood sits in its rising/falling tide
    # (computed from recent valence trajectory), or None when flat/calm
    tide: dict | None = None
    # A1: recent rate-of-change of felt valence; carries the downswing momentum
    # that gives a low mood a trough and a slow recovery. 0.0 when momentum is off.
    velocity: float = 0.0

    def to_dict(self) -> dict:
        return {
            "valence": round(self.valence, 4),
            "arousal": round(self.arousal, 4),
            "labels": list(self.labels),
            "aftertaste": self.aftertaste,
            "mixed_blend": self.mixed_blend,
            "tide": self.tide,
            "velocity": round(self.velocity, 4),
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> Mood:
        d = d or {}
        return cls(
            valence=_finite_stored(d.get("valence", 0.0), 0.0),
            arousal=_finite_stored(d.get("arousal", 0.4), 0.4),
            labels=list(d.get("labels") or []),
            aftertaste=d.get("aftertaste"),
            mixed_blend=d.get("mixed_blend"),
            tide=d.get("tide"),
            velocity=_finite_stored(d.get("velocity", 0.0), 0.0),
        )


# --------------------------------------------------------------------------- #
# Pressure — multi-bar accumulator (schema only; dynamics in affect.pressure) #
# --------------------------------------------------------------------------- #
BAR_NAMES = ("sadness", "anger", "anxiety", "boundary", "joy")


@dataclass
class PressureBars:
    """Five independent emotional pressure reservoirs, each in [0, 1].

    Emotion is not one scalar — sadness, anger, anxiety, boundary-violation and
    joy fill up separately, and whichever crosses threshold first is what gets
    released. See :mod:`feltstate.affect.pressure`.
    """

    sadness: float = 0.0
    anger: float = 0.0
    anxiety: float = 0.0
    boundary: float = 0.0
    joy: float = 0.0

    def to_dict(self) -> dict:
        return {k: round(getattr(self, k), 3) for k in BAR_NAMES}

    @classmethod
    def from_dict(cls, d: dict | None) -> PressureBars:
        d = d or {}
        return cls(**{k: _finite_stored(d.get(k, 0.0), 0.0) for k in BAR_NAMES})

    def max_bar(self) -> tuple[str, float]:
        """Return ``(bar_name, value)`` for the bar currently carrying the most
        pressure. Used by the pressure cooker to determine phase transitions —
        whether to move into ``building`` or out of it is keyed on the max bar."""
        return max(((k, getattr(self, k)) for k in BAR_NAMES), key=lambda x: x[1])

    def at_or_above(self, threshold: float) -> list[tuple[str, float]]:
        """Return every ``(bar_name, value)`` pair whose value is at or above
        ``threshold``, sorted highest-first. Used by :func:`~feltstate.affect.pressure._select_release`
        to decide which bars have crossed the release threshold and therefore should
        drive a release (or hybrid / collapse) this tick."""
        out = [(k, getattr(self, k)) for k in BAR_NAMES if getattr(self, k) >= threshold]
        out.sort(key=lambda x: x[1], reverse=True)
        return out


@dataclass
class PressureState:
    """Where the pressure cooker is in its release cycle.

    Phases: ``calm`` -> ``building`` (a bar climbs) -> ``releasing`` (a bar
    crossed threshold; 1-2 turns of strong expression) -> ``aftertaste`` (the
    feeling lingers) -> ``calm`` (bars settle to a floor, not zero).
    """

    bars: PressureBars = field(default_factory=PressureBars)
    phase: str = "calm"  # calm | building | releasing | aftertaste
    release_type: str | None = (
        None  # e.g. tears | anger | anxious | withdraw | burst_joy | collapse
    )
    release_secondary: str | None = None  # for hybrid (two bars released together)
    release_started_ts: str | None = None
    release_ends_ts: str | None = None
    aftertaste_until_ts: str | None = None
    last_tick_ts: str | None = None
    history: list[dict] = field(default_factory=list)  # last 5 release events
    # Plasticity (see PressureConfig.plasticity): per-bar sensitivity carved by
    # lived hits, healing toward the 0.5 baseline on a safety-paced clock.
    # Missing keys read as neutral 0.5.
    sensitivity: dict = field(default_factory=dict)
    sens_last_decay_ts: str | None = None  # healing anchor (advances every pass)

    def to_dict(self) -> dict:
        return {
            "bars": self.bars.to_dict(),
            "phase": self.phase,
            "release_type": self.release_type,
            "release_secondary": self.release_secondary,
            "release_started_ts": self.release_started_ts,
            "release_ends_ts": self.release_ends_ts,
            "aftertaste_until_ts": self.aftertaste_until_ts,
            "last_tick_ts": self.last_tick_ts,
            "history": list(self.history)[-5:],
            "sensitivity": dict(self.sensitivity),
            "sens_last_decay_ts": self.sens_last_decay_ts,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> PressureState:
        d = d or {}
        return cls(
            bars=PressureBars.from_dict(d.get("bars")),
            phase=d.get("phase", "calm"),
            release_type=d.get("release_type"),
            release_secondary=d.get("release_secondary"),
            release_started_ts=d.get("release_started_ts"),
            release_ends_ts=d.get("release_ends_ts"),
            aftertaste_until_ts=d.get("aftertaste_until_ts"),
            last_tick_ts=d.get("last_tick_ts"),
            history=list(d.get("history") or [])[-5:],
            sensitivity=dict(d.get("sensitivity") or {}),
            sens_last_decay_ts=d.get("sens_last_decay_ts"),
        )


# --------------------------------------------------------------------------- #
# The complete affective state                                                        #
# --------------------------------------------------------------------------- #
@dataclass
class AffectState:
    """An agent's complete felt inner state. Persists to one JSON file.

    This is what an :class:`~feltstate.engine.Engine` integrates over time and
    what :mod:`feltstate.render` translates into a first-person block the agent
    receives in first-person form as additional context.
    """

    mood: Mood = field(default_factory=Mood)
    traits: Traits = field(default_factory=Traits)
    relationship: Relationship = field(default_factory=Relationship)
    pressure: PressureState = field(default_factory=PressureState)
    last_tick_ts: str | None = None
    # persist-generation stamp (v0.2.1): bumps on every save() so operators can
    # detect cross-file skew (e.g. a sidecar store restored from an older
    # snapshot than state.json). Old files without it load as generation 0.
    generation: int = 0
    # rolling window of recent readings: [{"ts","valence","arousal","labels"}, ...]
    history: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "mood": self.mood.to_dict(),
            "traits": self.traits.to_dict(),
            "relationship": self.relationship.to_dict(),
            "pressure": self.pressure.to_dict(),
            "last_tick_ts": self.last_tick_ts,
            "generation": int(self.generation or 0),
            "history": list(self.history)[-50:],
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> AffectState:
        d = d or {}
        return cls(
            mood=Mood.from_dict(d.get("mood")),
            traits=Traits.from_dict(d.get("traits")),
            relationship=Relationship.from_dict(d.get("relationship")),
            pressure=PressureState.from_dict(d.get("pressure")),
            last_tick_ts=d.get("last_tick_ts"),
            generation=int(d.get("generation") or 0),
            history=list(d.get("history") or [])[-50:],
        )

    # --- persistence (atomic write) ---
    def save(self, path: str | Path) -> None:
        # Bump the generation stamp on every persist (v0.2.1) — see field note.
        self.generation = int(self.generation or 0) + 1
        atomic_write_text(path, json.dumps(self.to_dict(), ensure_ascii=False, indent=2))

    @classmethod
    def load(cls, path: str | Path) -> AffectState:
        """Load a persisted state, or a fresh default if the file is absent.

        A **corrupt or unreadable** existing file is not silently reset — that
        would wipe an agent's whole temperament with no trace. Instead the bad
        file is *quarantined* (renamed to a timestamped ``.corrupt-<ts>`` sibling
        so the data is never lost) and a loud :class:`UserWarning` is emitted, and
        only then does a fresh default state boot. Recovering the affective state (if
        possible) is left to the operator, who now has both the warning and the
        preserved file to work from.
        """
        p = Path(path)
        if not p.is_file():
            return cls()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("state root must be a JSON object")
            return cls.from_dict(data)
        except (json.JSONDecodeError, OSError, ValueError, TypeError, AttributeError) as exc:
            cls._quarantine_corrupt(p, exc)
            return cls()

    @staticmethod
    def _quarantine_corrupt(p: Path, exc: Exception) -> None:
        """Move a corrupt state file aside and warn loudly. Never raises: the
        agent must still be able to boot, but the wipe must be *visible* and the
        original bytes preserved for recovery."""
        quarantined: Path | None = None
        try:
            dest = p.with_name(f"{p.name}.corrupt-{int(time.time())}")
            # Don't clobber an earlier quarantine from the same second.
            n = 1
            while dest.exists():
                dest = p.with_name(f"{p.name}.corrupt-{int(time.time())}.{n}")
                n += 1
            p.replace(dest)
            quarantined = dest
        except OSError:
            quarantined = None  # rename failed (locked/permissions) — still warn
        where = (
            f"quarantined to {quarantined.name}"
            if quarantined is not None
            else "could NOT be quarantined (left in place)"
        )
        warnings.warn(
            f"feltstate: state file {p!s} is corrupt/unreadable ({exc!r}); "
            f"{where}. Booting a fresh default state — the agent's saved "
            f"personality was NOT loaded. Recover from the preserved file if needed.",
            UserWarning,
            stacklevel=3,
        )
