"""feltstate.config — every tunable in one place.

In ad-hoc implementations these constants tend to scatter across dozens of files;
pulling them into frozen dataclasses means you tune behaviour by editing config,
not by hunting through logic. Every magic number below has a one-line rationale
so you know what moving it does.

Nothing here is character-specific. Personality is expressed through
:class:`PersonaDials` (passed in per agent), not through code.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# --------------------------------------------------------------------------- #
# Personality dials — the only per-character knobs. 0.5 = neutral.            #
# --------------------------------------------------------------------------- #
@dataclass
class PersonaDials:
    """How a particular character *expresses* what it feels. These bias release
    style and tone; they do not change what the character feels (that comes from
    the readings). Supply one per agent; defaults give a balanced temperament."""

    warmth: float = 0.5  # higher -> softer, less likely to vent anger
    restraint: float = 0.5  # higher -> holds feelings in
    vulnerability: float = 0.5  # higher -> readier to show hurt (tears over withdraw)
    directness: float = 0.5  # higher -> says the hard thing plainly
    boundary_strength: float = 0.5  # higher -> withdraws / draws lines under pressure
    emotional_explicitness: float = 0.5  # higher -> names feelings out loud


# --------------------------------------------------------------------------- #
# Traits — asymmetric hedonic adaptation                                      #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TraitConfig:
    ewma_alpha: float = 0.08  # rise speed for all traits (~9-tick half-life)
    clamp_lo: float = 0.05  # never let a trait pin fully to 0/1 — one signal
    clamp_hi: float = 0.95  #   should always be able to nudge it
    # Asymmetric relaxation back to 0.5 baseline. This asymmetry *is* the model
    # of "good moods fade fast, bad moods linger" (hedonic adaptation + rumination).
    baseline_pull: dict = field(
        default_factory=lambda: {
            "depression": 0.005,  # sticky — sadness lingers
            "anxiety": 0.005,  # sticky
            "optimism": 0.040,  # fades ~8x faster than depression lingers
            "curiosity": 0.030,  # fades once the novelty is gone
        }
    )
    baseline: float = 0.5


@dataclass(frozen=True)
class MoodConfig:
    va_alpha: float = 0.20  # felt valence/arousal EWMA (faster than traits)
    # How hard traits pull the felt resting point. A depressed temperament gets
    # pulled toward a dim resting valence even when cheered.
    trait_gravity: float = 0.30
    aftertaste_weight: float = 0.5  # how much of last turn's flavour carries forward
    # Tide — the rising/falling shape of mood, read from recent valence history.
    tide_window: int = 5  # how many recent readings define the trajectory
    tide_delta: float = 0.06  # min valence change to count as rising/falling (else steady)
    # Label hysteresis: a new top label must persist this many ticks before it
    # replaces the shown one (anti-flicker; keeps the rendered block cache-stable).
    label_smooth_ticks: int = 2
    # --- A1: negative-channel momentum (a sulk has a trough + slow recovery) - #
    # Second-order inertia applied to felt valence on the way DOWN only: a dip
    # overshoots its target and recovers slowly, the way a bad mood doesn't lift
    # the instant the cause passes — while good moods stay on the plain fast EWMA
    # ("开心快淡"). 0.0 = off (plain EWMA both ways, current behaviour). 0.3–0.5 is
    # a believable trough without locking the mood in (kept well below the ~0.8
    # range where momentum stops recovering at all).
    momentum_mu: float = 0.0
    momentum_negative_only: bool = True  # momentum on downswings / negative only


# --------------------------------------------------------------------------- #
# Pressure — multi-bar release dynamics                                       #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PressureConfig:
    threshold_release: float = 0.85  # a bar at/above this triggers a release
    threshold_build_up: float = 0.70  # cross up into "building"
    threshold_build_down: float = 0.60  # fall below this back to "calm" (hysteresis)
    threshold_hybrid: float = 0.10  # two bars within this -> hybrid release (e.g. anger+tears)
    threshold_collapse: int = 3  # this many bars high at once -> incoherent collapse
    bar_floor: float = 0.40  # bars settle here after release, not to zero
    reset_keep: float = 0.15  # after aftertaste, bar = floor + (cur-floor)*this
    idle_decay: float = 0.018  # per-tick natural cooling
    power_threshold: float = 0.50  # power above this -> express; below -> suppress
    # Power = perceived control / self-efficacy (Lazarus appraisal, Bandura).
    # Weights sum to 1.0. High power -> dares to express; low -> suppresses.
    power_weights: dict = field(
        default_factory=lambda: {
            "optimism": 0.32,
            "depression_inv": 0.27,
            "anxiety_inv": 0.16,
            "safety": 0.15,
            "closeness": 0.10,
        }
    )
    # valence-opposite mutual inhibition: you don't laugh while crying.
    inhibition: float = 0.60
    # release duration (minutes, lo-hi) and lingering aftertaste (minutes) per type.
    release_duration_min: dict = field(
        default_factory=lambda: {
            "tears": (5, 15),
            "anger": (1, 3),
            "anxious": (5, 10),
            "withdraw": (30, 60),
            "burst_joy": (2, 5),
            "collapse": (10, 20),
        }
    )
    aftertaste_duration_min: dict = field(
        default_factory=lambda: {
            "tears": 30,
            "anger": 45,
            "anxious": 20,
            "withdraw": 60,
            "burst_joy": 15,
            "collapse": 90,
        }
    )


# Which pressure bar each release expresses, and its suppressed counterpart.
BAR_TO_RELEASE = {
    "sadness": "tears",
    "anger": "anger",
    "anxiety": "anxious",
    "boundary": "withdraw",
    "joy": "burst_joy",
}
BAR_TO_RELEASE_SUPPRESS = {
    "sadness": "tears_suppress",
    "anger": "anger_suppress",
    "anxiety": "anxious_suppress",
    "boundary": "withdraw_suppress",
    "joy": "burst_joy_suppress",
}


# --------------------------------------------------------------------------- #
# Memory — decaying 5W1H fact store                                           #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MemoryConfig:
    decay_per_day: float = 1.0 / 90.0  # intensity lost per day (90 days to fully fade 1.0)
    permanent_above: float = 0.85  # at/above this, never decays
    reinforce_boost: float = 0.10  # repeating a fact bumps its intensity
    recall_boost_each: float = 0.02  # each search hit slows decay a little ("used memory sticks")
    recall_boost_cap: float = 0.20
    visible_threshold: float = 0.30  # >= visible; >= archive_threshold archived; else forgotten
    archive_threshold: float = 0.10
    default_intensity: float = 0.50
    pending_intensity: float = 0.40  # "grey zone" (undecided) facts start lower

    # --- M1: affect-confidence per fact (evidence-weighted emotion) --------- #
    # A fact carries a {pos, neg, neu} confidence distribution + an evidence
    # weight. Repeating a fact still REINFORCES its salience (unchanged); this is
    # a *separate* signal that tells "repeated-and-emotional" from
    # "repeated-but-flat" — a catch-phrase stays neutral instead of accreting fake
    # weight. Lower prior weight = a young fact's feeling moves fast; as evidence
    # accrues it gains inertia (one stray message can't flip a settled feeling).
    sentiment_prior_weight: float = 1.0
    # Optional: dampen the SHOWN salience of low-charge (emotionally flat) facts so
    # frequent-but-flat noise doesn't crowd out meaningful memories — WITHOUT
    # capping the reinforce of charged facts. 0.0 = off (salience unchanged). When
    # > 0, a fully-flat fact's salience is multiplied by (1 - this).
    salience_charge_weight: float = 0.0

    # --- M2: importance-modulated decay curve ------------------------------ #
    # "linear" = the additive curve above (default, unchanged). "fsrs" = a
    # stretched-exponential v(t)=base*exp(-lambda*age^beta) whose rate slows with
    # importance and whose *shape* is asymmetric by valence: negative facts get a
    # fat slow tail (linger), positive ones fade faster — "开心快淡, 低落黏" for
    # memories too. Reinforce/recall feed importance; permanence still short-circuits.
    decay_curve: str = "linear"
    decay_lambda: float = 1.0 / 45.0  # base rate (per day^beta) at importance 0
    decay_importance_mu: float = 1.3  # importance slows decay: lambda *= exp(-mu*I)
    decay_beta_durable: float = 0.7  # beta for negative-valence facts (fat tail, linger)
    decay_beta_fast: float = 1.1  # beta for positive / flat facts (fade faster)

    # --- Skill region: a human-rated, self-growing capability store inside canon #
    # A "skill" is an ordinary 5W1H entry tagged region=="skill" plus a parallel
    # "skill" sub-dict holding HUMAN 1/2/3 ratings (1=lousy, 2=ok, 3=excellent).
    # Verification attaches ONLY to this region; fact/emotion entries are never
    # scored or gated. A skill's value is the human's real-use verdict — never
    # self-reported, never "did it run", never a function of recall frequency.
    skill_seed: float = 0.25  # prior utility of an unrated skill (the mean shrinks toward this)
    skill_prior_n: float = 2.0  # pseudo-count: how much evidence the prior is worth (inertia)
    skill_base_cap: float = 0.84  # < permanent_above: a skill may never become permanent
    skill_promote_excellent_count: int = (
        3  # this many "3" ratings (and no "1") -> auto-promote to main
    )
    skill_retire_bad_count: int = 3  # this many "1" ratings -> retire (lousy in real use)
    # Gray decay: slow on purpose (slower than a dream) — a candidate gets a long
    # lease to be tried and rated a few times before it fades from disuse.
    skill_gray_decay_per_day: float = 1.0 / 180.0  # ~6-month fade if never used (vs 90d for facts)
    # Selection is probabilistic, weighted by rating (explore/exploit): proven
    # skills win most, but a low one keeps a non-zero chance to be re-tried and
    # redeem itself, so no skill permanently monopolizes.
    skill_select_floor: float = 0.05  # non-zero selection-weight floor (the exploration tap)
    skill_select_temperature: float = 0.5  # >0; lower = exploit (steep), higher = explore (flat)
    skill_select_promoted_boost: float = (
        2.0  # confirmed skills weigh this much more than their utility
    )
    # Rating UX: never nag. At most one rating request per cooldown, capped per day.
    rating_cooldown_s: float = 600.0  # 10-min cooldown between rating requests
    rating_daily_cap: int = 8  # max rating requests per day


# --------------------------------------------------------------------------- #
# Time awareness — fuzzy distance, precise now                                #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TimeConfig:
    gate_minutes: float = 30.0  # below this gap, emit nothing (short-term sense is the model's own)
    # The model can estimate short gaps finely; estimates coarsen with distance.
    # Each entry: (upper_bound, phrase). First bound the gap fits under wins.
    # Bounds are in MINUTES. Beyond the last, fall back to an absolute date.
    distance_ladder_min: tuple = (
        (45, "half an hour"),
        (60, "almost an hour"),
        (75, "about an hour"),
        (90, "an hour or so"),
        (132, "almost two hours"),
        (168, "a couple of hours"),
        (210, "a few hours"),
        (360, "several hours"),
        (600, "most of a day"),
        (960, "almost a day"),
        (1800, "about a day"),
        (60 * 24 * 1.8, "a day or so"),
        (60 * 24 * 2.5, "a couple of days"),
        (60 * 24 * 4, "a few days"),
        (60 * 24 * 6.5, "several days"),
        (60 * 24 * 9, "about a week"),
        (60 * 24 * 18, "a couple of weeks"),
        (60 * 24 * 40, "a few weeks"),
        (60 * 24 * 75, "over a month"),
    )


# --------------------------------------------------------------------------- #
# Default label vocabulary and how labels map onto bars / traits              #
# --------------------------------------------------------------------------- #
# A compact, general-purpose emotion vocabulary (extend freely). An AffectSource
# emits 0-3 of these per turn; the maps below route them into pressure and traits.
DEFAULT_LABELS = (
    "focused",
    "curious",
    "content",
    "amused",
    "joyful",
    "excited",
    "proud",
    "grateful",
    "calm",
    "relieved",
    "hopeful",
    "tender",
    "sad",
    "lonely",
    "tired",
    "numb",
    "disappointed",
    "wistful",
    "anxious",
    "worried",
    "scared",
    "tense",
    "restless",
    "frustrated",
    "irritated",
    "angry",
    "indignant",
    "embarrassed",
    "surprised",
    "confused",
    "neutral",
)

# label -> {bar: per-turn increment}. Multiple labels onto one bar take max(),
# so they never double-count.
LABEL_TO_PRESSURE = {
    "sad": {"sadness": 0.013},
    "lonely": {"sadness": 0.013},
    "tired": {"sadness": 0.006},
    "numb": {"sadness": 0.008},
    "disappointed": {"sadness": 0.010},
    "wistful": {"sadness": 0.005, "joy": 0.003},
    "anxious": {"anxiety": 0.013},
    "worried": {"anxiety": 0.010},
    "scared": {"anxiety": 0.013},
    "tense": {"anxiety": 0.011},
    "restless": {"anxiety": 0.008},
    "frustrated": {"anger": 0.015},
    "irritated": {"anger": 0.010},
    "angry": {"anger": 0.015},
    "indignant": {"anger": 0.012},
    "curious": {"joy": 0.008},
    "focused": {"joy": 0.004},
    "hopeful": {"joy": 0.008},
    "content": {"joy": 0.012},
    "amused": {"joy": 0.020},
    "joyful": {"joy": 0.025},
    "excited": {"joy": 0.025},
    "proud": {"joy": 0.020},
    "grateful": {"joy": 0.015},
    "calm": {"joy": 0.006},
    "relieved": {"joy": 0.010},
    "tender": {"joy": 0.012},
    "embarrassed": {"anxiety": 0.007},
    "surprised": {"anxiety": 0.012, "joy": 0.012},
    "confused": {"anxiety": 0.006},
}

# label -> {trait: signal in [0,1]} fed to the trait EWMA. max() per trait.
LABEL_TO_TRAITS = {
    "sad": {"depression": 1.0},
    "lonely": {"depression": 0.9},
    "tired": {"depression": 0.7},
    "numb": {"depression": 0.8},
    "disappointed": {"depression": 0.7},
    "anxious": {"anxiety": 1.0},
    "worried": {"anxiety": 0.8},
    "scared": {"anxiety": 1.0},
    "tense": {"anxiety": 0.8},
    "restless": {"anxiety": 0.6},
    "frustrated": {"depression": 0.4, "anxiety": 0.4},
    "joyful": {"optimism": 1.0},
    "excited": {"optimism": 0.9},
    "content": {"optimism": 0.7},
    "hopeful": {"optimism": 0.9},
    "grateful": {"optimism": 0.7},
    "proud": {"optimism": 0.7},
    "amused": {"optimism": 0.6},
    "relieved": {"optimism": 0.5},
    "curious": {"curiosity": 1.0},
    "focused": {"curiosity": 0.6},
    "surprised": {"curiosity": 0.5},
}


# --------------------------------------------------------------------------- #
# Relationship — how the bond with the user drifts over time                  #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RelationshipConfig:
    """Rates at which the relationship dimensions move. All small on purpose: a
    bond is built (and frayed) slowly, over many turns, not in one exchange."""

    closeness_up: float = 0.004  # a warm turn nudges closeness up
    closeness_down: float = 0.003  # a cold turn nudges it down
    trust_up: float = 0.002  # warmth slowly earns trust
    safety_up: float = 0.003  # warmth slowly builds felt safety
    safety_down: float = 0.005  # friction erodes it a bit faster
    tension_decay: float = 0.01  # unresolved tension eases on its own per tick
    repair_per_event: float = 0.10  # repair only accumulates — it is trust capital
    # Milestone-driven shifts, scaled by the event's severity.
    trust_per_care: float = 0.03
    trust_per_betrayal: float = 0.08  # subtracted from trust
    closeness_per_warmth: float = 0.04
    tension_per_conflict: float = 0.15
    clamp_lo: float = 0.05
    clamp_hi: float = 0.95


# --------------------------------------------------------------------------- #
# Dream — illogical recombination and the faint residue it leaves             #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DreamConfig:
    """Tunables for :mod:`feltstate.dream`. The residue is meant to stay a wisp;
    raise ``residue_scale`` only if you want dreams to colour the next session
    more strongly."""

    min_fragments: int = 3  # fewest images a dream draws
    max_fragments: int = 5  # most images a dream draws
    dissonance_arousal: float = 0.15  # clashing material -> more activated
    dissonance_murk: float = 0.30  # clashing material -> valence pulled toward neutral
    residue_scale: float = 0.25  # shrink the blended affect to a faint nudge


# --------------------------------------------------------------------------- #
# Sleep pressure — when to dream (homeostatic, not clock-driven)              #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TirednessConfig:
    """Tunables for the single sleep-pressure accumulator (:mod:`feltstate.sleep`).

    Defaults aim for roughly one dream per day under ordinary activity, with a
    hard floor that prevents more than a couple regardless of how hard the agent
    lived. Tune ``rise_k`` for cadence, ``refractory_hours`` for the hard cap."""

    rise_k: float = (
        0.125  # level gained per hour per unit arousal (~16h at arousal .5 -> threshold)
    )
    threshold: float = 1.0  # a dream becomes possible at/above this level
    refractory_hours: float = 10.0  # hard minimum between dreams — caps frequency whatever the rate
    idle_gate_minutes: float = 30.0  # must be alone at least this long to drift off
    self_accel_alpha: float = (
        0.0  # >0: tiredness compounds (the tireder, the faster); off by default
    )
    level_cap: float = 3.0  # never accrue unboundedly across a long idle


@dataclass(frozen=True)
class Config:
    """Bundle of every sub-config. Pass a customised one to the engine, or use
    defaults."""

    traits: TraitConfig = field(default_factory=TraitConfig)
    mood: MoodConfig = field(default_factory=MoodConfig)
    pressure: PressureConfig = field(default_factory=PressureConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    time: TimeConfig = field(default_factory=TimeConfig)
    relationship: RelationshipConfig = field(default_factory=RelationshipConfig)
    dream: DreamConfig = field(default_factory=DreamConfig)
    tiredness: TirednessConfig = field(default_factory=TirednessConfig)


DEFAULT_CONFIG = Config()
