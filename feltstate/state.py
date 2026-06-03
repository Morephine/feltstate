"""feltstate.state — core data schemas (the contract shared by every module).

An :class:`AffectState` is an agent's *felt inner state*: how it feels right now
(``mood``), its long-term temperament (``traits``), how it feels about the person
it is talking to (``relationship``), and the pressure-cooker of accumulated
emotion (``pressure``).

These are plain dataclasses with JSON round-tripping and **no behaviour**. The
dynamics live in :mod:`feltstate.affect`. Keeping every schema in one module
lets the dynamics, memory, render, and source layers agree on shape without
import cycles.

Design note — *ground truth, not self-report*: an :class:`AffectDelta` is
**measured** for each turn by an :class:`~feltstate.sources.base.AffectSource`,
not asked of the generating model. The model never gets to decide how it feels;
it only gets to read the felt state back (see :mod:`feltstate.render`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# --------------------------------------------------------------------------- #
# Per-turn reading                                                            #
# --------------------------------------------------------------------------- #
@dataclass
class AffectDelta:
    """One turn's affect reading — how the agent feels in reaction to the latest
    input, as *measured* by an :class:`~feltstate.sources.base.AffectSource`.

    This is the ground-truth signal. It is the only place raw per-turn emotion
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
            valence=float(d.get("valence", 0.0)),
            arousal=float(d.get("arousal", 0.4)),
            labels=list(d.get("labels") or []),
            confidence=float(d.get("confidence", 0.7)),
            monologue=str(d.get("monologue", "") or ""),
            anticipation=d.get("anticipation"),
            mixed_blend=d.get("mixed_blend"),
            milestones=list(d.get("milestones") or []),
        )


# --------------------------------------------------------------------------- #
# Long-term temperament                                                       #
# --------------------------------------------------------------------------- #
@dataclass
class Traits:
    """Slow-moving personality dimensions in [0, 1] (0.5 = neutral baseline).

    Integrated from per-turn readings by an asymmetric EWMA: positive traits
    (optimism, curiosity) relax back to baseline several times faster than
    negative ones (depression, anxiety). That asymmetry is *hedonic adaptation*
    and *rumination* — good moods fade, bad ones linger. See
    :mod:`feltstate.affect.traits`.
    """

    depression: float = 0.5
    optimism: float = 0.5
    anxiety: float = 0.5
    curiosity: float = 0.5

    def to_dict(self) -> dict:
        return {
            k: round(getattr(self, k), 4)
            for k in ("depression", "optimism", "anxiety", "curiosity")
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> Traits:
        d = d or {}
        return cls(
            **{k: float(d.get(k, 0.5)) for k in ("depression", "optimism", "anxiety", "curiosity")}
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
            closeness=float(d.get("closeness", 0.5)),
            trust=float(d.get("trust", 0.5)),
            safety=float(d.get("safety", 0.5)),
            unresolved_tension=float(d.get("unresolved_tension", 0.0)),
            repair_history=float(d.get("repair_history", 0.0)),
        )


# --------------------------------------------------------------------------- #
# Mood — the felt continuous state                                            #
# --------------------------------------------------------------------------- #
@dataclass
class Mood:
    """The fast-moving felt state. ``valence``/``arousal`` are smoothed EWMAs of
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

    def to_dict(self) -> dict:
        return {
            "valence": round(self.valence, 4),
            "arousal": round(self.arousal, 4),
            "labels": list(self.labels),
            "aftertaste": self.aftertaste,
            "mixed_blend": self.mixed_blend,
            "tide": self.tide,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> Mood:
        d = d or {}
        return cls(
            valence=float(d.get("valence", 0.0)),
            arousal=float(d.get("arousal", 0.4)),
            labels=list(d.get("labels") or []),
            aftertaste=d.get("aftertaste"),
            mixed_blend=d.get("mixed_blend"),
            tide=d.get("tide"),
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
        return cls(**{k: float(d.get(k, 0.0)) for k in BAR_NAMES})

    def max_bar(self) -> tuple[str, float]:
        return max(((k, getattr(self, k)) for k in BAR_NAMES), key=lambda x: x[1])

    def at_or_above(self, threshold: float) -> list[tuple[str, float]]:
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
        )


# --------------------------------------------------------------------------- #
# The whole felt state                                                        #
# --------------------------------------------------------------------------- #
@dataclass
class AffectState:
    """An agent's complete felt inner state. Persists to one JSON file.

    This is what an :class:`~feltstate.engine.Engine` integrates over time and
    what :mod:`feltstate.render` translates into a first-person block the agent
    reads back as *its own* feelings.
    """

    mood: Mood = field(default_factory=Mood)
    traits: Traits = field(default_factory=Traits)
    relationship: Relationship = field(default_factory=Relationship)
    pressure: PressureState = field(default_factory=PressureState)
    last_tick_ts: str | None = None
    # rolling window of recent readings: [{"ts","valence","arousal","labels"}, ...]
    history: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "mood": self.mood.to_dict(),
            "traits": self.traits.to_dict(),
            "relationship": self.relationship.to_dict(),
            "pressure": self.pressure.to_dict(),
            "last_tick_ts": self.last_tick_ts,
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
            history=list(d.get("history") or [])[-50:],
        )

    # --- persistence (atomic write) ---
    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)

    @classmethod
    def load(cls, path: str | Path) -> AffectState:
        p = Path(path)
        if not p.is_file():
            return cls()
        try:
            return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            return cls()
