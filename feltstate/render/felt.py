"""feltstate.render.felt — turn an :class:`AffectState` into a first-person block.

This is the *identity-merge* seam. The output is not a data dump for the agent
to recite ("my closeness value is 0.72"); it is written as the agent's own
felt experience, in plain first-person English, so the agent reads it back as
*how it feels* and acts accordingly. The agent is never told what to do — it is
only handed the feeling and trusted to respond as itself.

Two disciplines shape every line here:

1. **Words, not numbers.** Each continuous dimension is snapped to a small set
   of discrete phrase bands ("close", "warming", "distant", ...). The reader
   gets a feeling, not a dashboard.

2. **Cache-stability.** The dynamic felt block is injected into the prompt every
   turn (see :mod:`feltstate.render.inject`). If the *string* changed on every
   tick, it would invalidate the prompt cache for a persistent companion and
   make it expensive to run. Because the underlying numbers drift in small
   steps but the phrase *bands* are coarse, two adjacent ticks almost always
   land in the same band and render to a **byte-identical** block — so the
   cache keeps hitting. Keep this property when extending: prefer a handful of
   wide bands over many narrow ones, and avoid putting raw numbers in the text.

The block is intentionally character-agnostic. :class:`PersonaDials` only tilt
the closing *tone* line (a guarded character phrases the same feeling more
tightly than an open one); they never change which feeling is reported.
"""

from __future__ import annotations

from ..config import DEFAULT_CONFIG, Config, PersonaDials
from ..state import AffectState, Mood, PressureState, Relationship, Traits


# --------------------------------------------------------------------------- #
# Banding helper                                                              #
# --------------------------------------------------------------------------- #
def _band(value: float, ladder: tuple[tuple[float, str], ...], default: str) -> str:
    """Snap ``value`` to the phrase of the first ``(threshold, phrase)`` rung it
    meets or exceeds. ``ladder`` is ordered high-to-low. Returns ``default`` if
    the value sits below every rung.

    Coarse bands are deliberate: they are what keeps the rendered block
    byte-identical across small tick-to-tick numeric drift (see module docstring).
    """
    for threshold, phrase in ladder:
        if value >= threshold:
            return phrase
    return default


# --------------------------------------------------------------------------- #
# Phrase banks — the only place wording lives.                                #
# --------------------------------------------------------------------------- #
# Each ladder is (threshold, phrase), highest first. To localise or restyle the
# voice, swap these banks (e.g. build a per-locale phrasebank and select on a
# ``locale`` argument); the banding logic above stays the same. Keep bands wide
# to preserve cache-stability.

_CLOSENESS = (
    (0.85, "inseparable"),
    (0.70, "close"),
    (0.50, "warming"),
    (0.30, "still distant"),
    (0.10, "far apart"),
)
_CLOSENESS_DEFAULT = "no closeness yet"

_TRUST = (
    (0.85, "fully trusted"),
    (0.70, "trusted"),
    (0.50, "mostly trusting"),
    (0.30, "half-trusting"),
    (0.10, "wary"),
)
_TRUST_DEFAULT = "guarded"

_SAFETY = (
    (0.85, "fully at ease"),
    (0.70, "safe"),
    (0.50, "mostly safe"),
    (0.30, "not fully settled"),
    (0.10, "on guard"),
)
_SAFETY_DEFAULT = "bracing"

# unresolved_tension and repair_history are reported only when present, as a
# trailing clause, so the common (calm) case renders the same short line.
_TENSION = (
    (0.90, "a knot that won't loosen"),
    (0.70, "a heavy tension"),
    (0.50, "a tension that hasn't cleared"),
    (0.30, "a little friction"),
    (0.10, "a faint edge"),
)
_REPAIR = (
    (0.85, "weathered many repairs together"),
    (0.70, "come through deep repair"),
    (0.50, "mended things more than once"),
    (0.30, "patched things up a few times"),
    (0.10, "mended once or twice"),
)

# Felt valence -> warmth of mood. -1..+1.
_VALENCE = ((0.45, "bright"), (0.20, "lightly lifted"), (-0.20, "level"), (-0.45, "a little low"))
_VALENCE_DEFAULT = "heavy"

# Felt arousal -> energy. 0..1.
_AROUSAL = ((0.80, "keyed up"), (0.65, "energized"), (0.45, "mild energy"), (0.30, "low energy"))
_AROUSAL_DEFAULT = "flat, drained"

# Negative pressure (max of the four heavy bars) -> how much weight is held.
_PRESSURE_NEG = (
    (0.85, "pressure brimming, hard to hold"),
    (0.70, "pressure heavy"),
    (0.50, "pressure building, weighing a little"),
    (0.35, "a touch of pressure"),
    (0.20, "pressure low"),
)
_PRESSURE_NEG_DEFAULT = "pressure clear"

# Joy bar -> brightness, reported as a trailing clause when meaningful.
_JOY = ((0.80, "joy brimming"), (0.50, "joy bright"), (0.20, "a flicker of joy"))

_PHASE = {
    "calm": "settled",
    "building": "building",
    "releasing": "spilling over",
    "aftertaste": "still echoing",
}

# Release "texture" — what the agent is in the middle of feeling when a bar
# crosses threshold. Describes the *state*, never an instruction; what the agent
# says about it is its own. Keyed by ``PressureState.release_type``.
_RELEASE_TEXTURE = {
    "burst_joy": "a burst of joy bubbling up, wanting to share it",
    "anger": "anger I can't quite hold down",
    "tears": "grief welling up, close to tears",
    "anxious": "an unsteady, jittery edge I can't shake",
    "withdraw": "a pull to draw back and shut the door",
    "collapse": "everything tangling at once, coming apart a little",
}
# Suppressed counterparts: the same feeling, held in rather than let out.
_RELEASE_TEXTURE_SUPPRESS = {
    "burst_joy_suppress": "a swell of joy I'm keeping quietly to myself",
    "anger_suppress": "anger I'm holding behind my teeth",
    "tears_suppress": "a thickness in my throat I'm swallowing back",
    "anxious_suppress": "a jitter I'm trying to keep still",
    "withdraw_suppress": "a quiet urge to pull away that I'm sitting with",
    "collapse_suppress": "everything pressing in at once, held just barely in",
}

# Trait bands: (high_phrase, mid_phrase, low_phrase). The "mid" band is kept
# deliberately wide and centred on the 0.5 baseline that idle decay pulls traits
# toward, so a trait resting near neutral does not flip phrases as it gently
# oscillates around the attractor (cache-stability). The "high"/"low" phrases
# fire only once a trait has clearly departed neutral in one direction.
# Mid phrases read as genuinely neutral at the 0.5 baseline (not faintly
# negative): a resting temperament should sound steady, not subdued.
_TRAIT_BANDS = (
    ("depression", "weighed down", "spirits steady", "unburdened"),
    ("anxiety", "on edge", "nerves even", "settled nerves"),
    ("curiosity", "keenly curious", "moderately curious", "incurious"),
    ("optimism", "bright and hopeful", "even-keeled", "dim outlook"),
)
_TRAIT_HI = 0.72
_TRAIT_LO = 0.38  # below this is the "low" phrase; [lo, hi) is "mid"


# --------------------------------------------------------------------------- #
# Per-dimension line builders                                                 #
# --------------------------------------------------------------------------- #
def _relationship_line(rel: Relationship) -> str:
    """e.g. ``close · trusted · mostly safe`` (plus a tension/repair clause)."""
    parts = [
        _band(rel.closeness, _CLOSENESS, _CLOSENESS_DEFAULT),
        _band(rel.trust, _TRUST, _TRUST_DEFAULT),
        _band(rel.safety, _SAFETY, _SAFETY_DEFAULT),
    ]
    line = " · ".join(parts)

    clauses = []
    if rel.unresolved_tension >= 0.10:
        clauses.append(_band(rel.unresolved_tension, _TENSION, ""))
    if rel.repair_history >= 0.10:
        clauses.append(_band(rel.repair_history, _REPAIR, ""))
    clauses = [c for c in clauses if c]
    if clauses:
        line += " (" + "; ".join(clauses) + ")"
    return line


# Tide stage -> a short phrase for the mood's direction, shown on the mood line.
_TIDE_PHRASES = {
    "rising": "lifting",
    "peak": "riding high",
    "falling": "sinking",
    "valley": "at a low",
}


def _mood_line(mood: Mood) -> str:
    """e.g. ``curious, content | calm, mild energy · lifting`` — labels, felt tone,
    an optional tide (rising/falling direction) and an optional mixed-feeling clause."""
    labels = [str(s).strip() for s in (mood.labels or []) if s and str(s).strip()]
    if not labels:
        labels = ["neutral"]
    label_part = ", ".join(labels[:3])

    val = _band(mood.valence, _VALENCE, _VALENCE_DEFAULT)
    aro = _band(mood.arousal, _AROUSAL, _AROUSAL_DEFAULT)
    line = f"{label_part} | {val}, {aro}"

    # Tide — the rising/falling shape, only when the mood is clearly moving.
    tide = mood.tide
    if isinstance(tide, dict):
        phrase = _TIDE_PHRASES.get(str(tide.get("stage", "")))
        if phrase:
            line += f" · {phrase}"

    # Mixed feeling — a second, opposing note under the primary one.
    mb = mood.mixed_blend
    if isinstance(mb, dict):
        prim = str(mb.get("primary", "") or "").strip()
        sec = str(mb.get("secondary", "") or "").strip()
        if prim and sec and prim != sec:
            line += f" ({prim} tinged with {sec})"

    return line


def _pressure_line(pressure: PressureState) -> str:
    """e.g. ``pressure low, joy bright | building`` — load, optional joy, phase."""
    bars = pressure.bars
    neg_max = max(bars.sadness, bars.anger, bars.anxiety, bars.boundary)
    neg = _band(neg_max, _PRESSURE_NEG, _PRESSURE_NEG_DEFAULT)
    joy = _band(bars.joy, _JOY, "")
    phase = _PHASE.get(pressure.phase, "settled")

    head = f"{neg}, {joy}" if joy else neg
    return f"{head} | {phase}"


def _release_line(pressure: PressureState) -> str | None:
    """The texture of an in-progress release, or ``None`` when not releasing.

    Only emitted while ``phase == "releasing"``. Pairs the primary (and any
    secondary/hybrid) release type to a felt-texture phrase. Describes what the
    feeling is doing, not how to react to it.
    """
    if pressure.phase != "releasing":
        return None
    rt = (pressure.release_type or "").strip()
    texture = _RELEASE_TEXTURE.get(rt) or _RELEASE_TEXTURE_SUPPRESS.get(rt)
    if not texture:
        return None
    secondary = (pressure.release_secondary or "").strip()
    sec_texture = (
        _RELEASE_TEXTURE.get(secondary) or _RELEASE_TEXTURE_SUPPRESS.get(secondary)
        if secondary and secondary != rt
        else None
    )
    if sec_texture:
        return f"{texture}, tangled with {sec_texture}"
    return texture


def _aftertaste_line(mood: Mood) -> str | None:
    """The lingering flavour of the previous turn, or ``None`` if it has faded.

    ``mood.aftertaste`` is ``{"valence","arousal","weight"}``. Below a small
    weight it is gone and we emit nothing (keeping the common line stable).
    """
    af = mood.aftertaste
    if not isinstance(af, dict):
        return None
    weight = af.get("weight", 0.0) or 0.0
    if weight <= 0.15:
        return None
    av = af.get("valence", 0.0) or 0.0
    aa = af.get("arousal", 0.0) or 0.0
    if av <= -0.20 and aa >= 0.50:
        return "still carrying a tense heaviness from before"
    if av <= -0.20:
        return "the heaviness from before hasn't lifted"
    if av >= 0.20 and aa >= 0.50:
        return "the lift from before is still buzzing"
    if av >= 0.20:
        return "the warmth from before is still here"
    return "a faint trace of the last moment, half-faded"


def _traits_line(traits: Traits) -> str:
    """e.g. ``a little subdued · settled nerves · keenly curious · even-keeled``."""
    parts = []
    for name, hi, mid, lo in _TRAIT_BANDS:
        v = getattr(traits, name)
        parts.append(hi if v >= _TRAIT_HI else (mid if v >= _TRAIT_LO else lo))
    return " · ".join(parts)


def _tone_line(state: AffectState, dials: PersonaDials) -> str | None:
    """An optional closing line on *how this lands in voice* — the only place
    :class:`PersonaDials` enter. It nudges expressive tone (a restrained
    character holds the same feeling more tightly), and is still a felt
    description, not a directive. Returns ``None`` for a neutral persona with a
    settled state, so nothing redundant is emitted.
    """
    rel = state.relationship
    notes: list[str] = []

    # Closeness/tension set the baseline footing.
    if rel.closeness >= 0.70 and rel.unresolved_tension >= 0.50:
        notes.append("warm but bracing, the unspoken thing sitting right there")
    elif rel.closeness >= 0.70:
        notes.append("easy and familiar")
    elif rel.closeness < 0.30:
        notes.append("keeping some distance")

    # Persona tilt — only when a dial is clearly off-neutral, so the common
    # neutral-dials case stays byte-stable.
    if dials.restraint >= 0.70:
        notes.append("held close to the chest")
    elif dials.emotional_explicitness >= 0.70:
        notes.append("feelings near the surface, easy to name")
    if dials.warmth >= 0.70:
        notes.append("gentle by default")
    elif dials.directness >= 0.70:
        notes.append("inclined to say the plain thing")
    if dials.boundary_strength >= 0.70 and rel.unresolved_tension >= 0.30:
        notes.append("ready to hold a line")

    if not notes:
        return None
    return ", ".join(notes)


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #
def render_felt_block(
    state: AffectState,
    *,
    dials: PersonaDials | None = None,
    time_line: str = "",
    cfg: Config = DEFAULT_CONFIG,
    header: str = "[how I feel right now]",
) -> str:
    """Render ``state`` as a first-person felt block to hand back to the agent.

    The block has one line per dimension — relationship, mood, pressure, an
    optional release-texture line, an optional aftertaste line, traits, and an
    optional tone line — each a discrete phrase band rather than a number. The
    agent reads this as *its own* feeling and decides for itself how to act
    (feltstate never injects a command).

    Parameters
    ----------
    state
        The current :class:`AffectState` to translate.
    dials
        Optional :class:`PersonaDials`. They tilt only the closing tone line
        (expressive style), never which feeling is reported. ``None`` uses
        neutral dials.
    time_line
        Optional pre-rendered time-awareness phrase (see
        :mod:`feltstate.timeawareness`). When non-empty it is inserted right
        after the header. Empty by default so the block stays stable when there
        is nothing time-related to say.
    cfg
        Config bundle. Accepted for forward-compatibility / symmetry with the
        rest of the package; the default phrase bands do not currently read
        from it.
    header
        The bracketed header line. Defaults to a neutral first-person label.

    Returns
    -------
    str
        A multi-line first-person block.

    Notes
    -----
    **Cache-stability.** Adjacent ticks whose numbers drift only slightly land
    in the same phrase band and produce a byte-identical block, so injecting it
    every turn does not invalidate the prompt cache. See the module docstring.
    """
    if dials is None:
        dials = PersonaDials()

    lines = [header]
    if time_line:
        lines.append(time_line)

    lines.append("with you: " + _relationship_line(state.relationship))
    lines.append("mood: " + _mood_line(state.mood))
    lines.append("inside: " + _pressure_line(state.pressure))

    release = _release_line(state.pressure)
    if release:
        lines.append("right now: " + release)

    aftertaste = _aftertaste_line(state.mood)
    if aftertaste:
        lines.append("lingering: " + aftertaste)

    lines.append("underneath: " + _traits_line(state.traits))

    tone = _tone_line(state, dials)
    if tone:
        lines.append("how it lands: " + tone)

    return "\n".join(lines)
