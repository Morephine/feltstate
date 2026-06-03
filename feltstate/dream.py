"""feltstate.dream — give the agent dreams it can't explain.

This is the deliberately-illogical sibling of memory consolidation. It does *not*
mine experience into rational beliefs (that would be the agent's own job — it can
read its memory and reason). Instead it takes the agent's charged material —
desires, recent facts, emotional peaks — and **recombines it associatively,
without logic**, into a short, discontinuous dream. The dream itself is
ephemeral; what matters is the faint **affect residue** it leaves: the agent
wakes a little warm, or unsettled, or wistful, *with no cause it can point to*.

Why that is the point. Current AI affect is always *traceable* — you can explain
why the agent is "happy" (you just said something kind). Real inner lives have
moods with no traceable source (a bad night, a strange dream, nothing). An agent
that is occasionally, inexplicably, a little off — and, asked why, can only say
"I don't know, I had odd dreams" — reads as a separate mind in a way a pure
state machine never does. The dream is the mechanism that produces that
*authentic-but-unexplainable* mood: it is sourced from the agent's own real
material, just recombined so the causal thread is severed.

Design notes:

* **No LLM required.** Dreams are *meant* to be incoherent, and incoherence is
  exactly what a language model is bad at faking (it writes coherent stories).
  Pure template recombination of real, affect-tagged fragments is structurally a
  dream. The whole pipeline — gather, stitch, residue — is standard library.
* **The LLM is optional and lazy.** If, and only if, the agent ever puts a dream
  into words ("I had a strange dream about…"), a model can polish the crude
  stitch on demand (see :func:`polish_hook` usage). Most dreams are never spoken,
  so most cost nothing.
* **Tool, not controller.** A dream produces *state* (a text fragment and a small
  felt residue), never an instruction. What the agent does with a vague mood is
  its own.
* **Locale.** The default :data:`DEFAULT_PHRASEBOOK` is English. Dream grammar is
  language-specific, so supply your own :class:`Phrasebook` for another language.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .config import DreamConfig
from .state import AffectState

__all__ = [
    "Fragment",
    "Phrasebook",
    "Dream",
    "DEFAULT_PHRASEBOOK",
    "stitch",
    "residue",
    "dream",
    "gather_fragments",
]


# --------------------------------------------------------------------------- #
# Raw material                                                                #
# --------------------------------------------------------------------------- #
@dataclass
class Fragment:
    """One piece of dream material: a short image/phrase plus the affect it
    carries (measured when it was first stored) and a salience weight.

    Supply rich fragments for vivid dreams (a desire, a remembered scene, an
    emotional peak — each with the valence/arousal it was felt at). The bundled
    :func:`gather_fragments` makes a best-effort set from an :class:`AffectState`,
    but the interesting material usually comes from the caller's own store.
    """

    text: str
    valence: float = 0.0
    arousal: float = 0.4
    weight: float = 1.0  # recency / salience; higher = more likely to be drawn


# --------------------------------------------------------------------------- #
# Dream grammar (language-specific; swap for other locales)                   #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Phrasebook:
    """The connectives a dream is stitched from. All language-specific.

    ``morph`` / ``juxtapose`` / ``jump`` are bridges placed *between* fragments;
    they must read as continuing from the previous image without re-naming it
    (e.g. "which became", not "{previous} became"). ``open`` starts the dream and
    ``dissolve`` ends it (dreams don't conclude, they slip away).

    ``joiner`` separates the stitched parts and ``glue`` sits between a connective
    and the fragment it introduces — both default to English word-spacing. For a
    space-less script (Chinese, Japanese) set ``glue=""`` and ``joiner`` to your
    own punctuation.
    """

    open: tuple[str, ...]
    morph: tuple[str, ...]
    juxtapose: tuple[str, ...]
    jump: tuple[str, ...]
    dissolve: tuple[str, ...]
    joiner: str = ", "
    terminator: str = "."
    glue: str = " "  # between a connective and its fragment ("" for CJK scripts)


DEFAULT_PHRASEBOOK = Phrasebook(
    open=("I was", "I was somewhere with", "I kept trying to get back to"),
    morph=("which became", "that turned into", "and it bled into", "and somewhere in it was"),
    juxtapose=("and underneath it,", "and at the same time,", "and tangled with it,"),
    jump=("and then", "suddenly", "the scene slid to", "then out of nowhere"),
    dissolve=("and it kept slipping", "and I couldn't hold onto it", "and I lost the thread"),
)


@dataclass
class Dream:
    """The product of one dream: its (ephemeral) text and the felt residue it
    leaves. ``valence``/``arousal`` are small nudges to apply to the mood;
    ``dissonance`` is how much the dreamed material clashed (high = an uneasy,
    hard-to-place dream)."""

    text: str
    valence: float
    arousal: float
    dissonance: float
    fragments: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Stitch — the illogical recombination                                        #
# --------------------------------------------------------------------------- #
def stitch(fragments: list[Fragment], phrasebook: Phrasebook, rng: random.Random) -> str:
    """Recombine ``fragments`` into one discontinuous dream string.

    Each fragment appears once, bridged to the next by a randomly chosen
    connective (morph / juxtapose / jump); the same connective is not used twice
    in a row. The result opens, jump-cuts between images, and dissolves — it does
    not resolve. Returns ``""`` for no fragments.
    """
    if not fragments:
        return ""
    parts = [rng.choice(phrasebook.open) + phrasebook.glue + fragments[0].text]
    last = ""
    for frag in fragments[1:]:
        r = rng.random()
        pool = (
            phrasebook.morph if r < 0.40 else phrasebook.juxtapose if r < 0.65 else phrasebook.jump
        )
        bridge = rng.choice(pool)
        tries = 0
        while bridge == last and tries < 5:
            bridge = rng.choice(pool)
            tries += 1
        parts.append(bridge + phrasebook.glue + frag.text)
        last = bridge
    parts.append(rng.choice(phrasebook.dissolve))
    body = phrasebook.joiner.join(parts)
    return body + phrasebook.terminator


# --------------------------------------------------------------------------- #
# Residue — the faint, untraceable mood the dream leaves                      #
# --------------------------------------------------------------------------- #
def residue(
    fragments: list[Fragment], cfg: DreamConfig | None = None
) -> tuple[float, float, float]:
    """Compute the felt residue ``(valence, arousal, dissonance)`` of a dream.

    The residue is a *charge-weighted* blend of the fragments' own affect, scaled
    down to a wisp. When the dreamed fragments clash in valence (a longing next to
    a fear), ``dissonance`` is high: that lifts arousal and muddies valence toward
    neutral — the texture of an uneasy, ambivalent dream — without any model
    interpreting it. Returns zeros for no fragments.
    """
    cfg = cfg or DreamConfig()
    if not fragments:
        return 0.0, 0.0, 0.0
    weights = [abs(f.valence) + 0.1 for f in fragments]
    total = sum(weights) or 1.0
    val = sum(f.valence * w for f, w in zip(fragments, weights, strict=True)) / total
    aro = sum(f.arousal * w for f, w in zip(fragments, weights, strict=True)) / total

    vals = [f.valence for f in fragments]
    dissonance = max(vals) - min(vals)
    aro += dissonance * cfg.dissonance_arousal
    val *= 1.0 - dissonance * cfg.dissonance_murk

    res_v = round(val * cfg.residue_scale, 4)
    res_a = round((aro - 0.4) * cfg.residue_scale, 4)
    return res_v, res_a, round(dissonance, 3)


# --------------------------------------------------------------------------- #
# The dream itself                                                            #
# --------------------------------------------------------------------------- #
def dream(
    fragments: list[Fragment],
    *,
    phrasebook: Phrasebook = DEFAULT_PHRASEBOOK,
    cfg: DreamConfig | None = None,
    rng: random.Random | None = None,
) -> Dream:
    """Draw, stitch, and weigh one dream from ``fragments``.

    Samples up to ``cfg.max_fragments`` of them (charge × weight biased — dreams
    over-sample what is vivid and recent), stitches them into an illogical
    sequence, and computes the residue. Pass a seeded ``rng`` for reproducible
    dreams; omit it for genuine variation.
    """
    cfg = cfg or DreamConfig()
    rng = rng or random.Random()

    pool = [f for f in fragments if f.text and f.text.strip()]
    if not pool:
        return Dream(text="", valence=0.0, arousal=0.0, dissonance=0.0, fragments=[])

    k = min(len(pool), rng.randint(cfg.min_fragments, cfg.max_fragments))
    # Weighted draw without replacement, biased by emotional charge and salience.
    drawn: list[Fragment] = []
    candidates = list(pool)
    for _ in range(k):
        weights = [(abs(f.valence) + 0.1) * max(0.0, f.weight) for f in candidates]
        if sum(weights) <= 0:
            pick = rng.choice(candidates)
        else:
            pick = rng.choices(candidates, weights=weights, k=1)[0]
        drawn.append(pick)
        candidates.remove(pick)

    text = stitch(drawn, phrasebook, rng)
    res_v, res_a, diss = residue(drawn, cfg)
    return Dream(
        text=text,
        valence=res_v,
        arousal=res_a,
        dissonance=diss,
        fragments=[f.text for f in drawn],
    )


# --------------------------------------------------------------------------- #
# Best-effort fragment gathering from an AffectState                          #
# --------------------------------------------------------------------------- #
# Pressure releases -> a short felt image + the affect of that climax. Generic
# English; supply richer Fragments via ``extra`` for another locale or voice.
_RELEASE_FRAGMENT = {
    "burst_joy": ("a flare of joy", 0.7, 0.7),
    "tears": ("a wave of tears", -0.6, 0.6),
    "anger": ("a flash of anger", -0.5, 0.75),
    "anxious": ("a clench of dread", -0.5, 0.7),
    "withdraw": ("the urge to pull away", -0.4, 0.35),
    "collapse": ("everything crowding in at once", -0.4, 0.8),
}


def gather_fragments(
    state: AffectState,
    *,
    extra: list[Fragment] | None = None,
    history_window: int = 30,
    peak_valence: float = 0.25,
    max_history: int = 6,
    include_releases: bool = True,
    max_releases: int = 4,
) -> list[Fragment]:
    """A best-effort dream-material set drawn from ``state`` plus any ``extra``.

    Three layers, all tagged with the affect they were felt at:

    * ``extra`` — rich :class:`Fragment` objects from the caller's own store (real
      desires, remembered scenes, an inner monologue). The best material; supply
      it when you can.
    * **history peaks** — turns whose valence ran past ``peak_valence``, labelled
      by their top emotion word. Thin (labels make abstract dreams) but free.
    * **pressure releases** — the agent's recent emotional *climaxes* (a good cry,
      a flare of joy), pulled from the pressure cooker as raw spikes. Distinct
      from the reflective history peaks: a release is something it *felt break*.

    Recency weights each fragment, so older material fades to a faint tail rather
    than vanishing — which is what lets an old, charged image recur in a later
    dream. Intentionally best-effort: the vivid stuff is ``extra``.
    """
    frags: list[Fragment] = list(extra or [])

    history = list(state.history or [])[-history_window:]
    n = len(history)
    peaks: list[Fragment] = []
    for i, h in enumerate(history):
        if not isinstance(h, dict):
            continue
        v = float(h.get("valence", 0.0))
        if abs(v) < peak_valence:
            continue
        labels = [str(x) for x in (h.get("labels") or []) if x]
        text = labels[0] if labels else ("a bright moment" if v > 0 else "a low moment")
        recency = (i + 1) / max(1, n)  # later in the window = fresher = heavier
        peaks.append(
            Fragment(text=text, valence=v, arousal=float(h.get("arousal", 0.4)), weight=recency)
        )

    # Keep the strongest peaks.
    peaks.sort(key=lambda f: abs(f.valence) * f.weight, reverse=True)
    frags.extend(peaks[:max_history])

    # Pressure releases — raw emotional climaxes, charged dream material.
    if include_releases:
        releases = list(getattr(state.pressure, "history", None) or [])[-max_releases:]
        rn = len(releases)
        for i, ev in enumerate(releases):
            if not isinstance(ev, dict):
                continue
            rtype = str(ev.get("release_type") or "").replace("_suppress", "")
            spec = _RELEASE_FRAGMENT.get(rtype)
            if not spec:
                continue
            text, rv, ra = spec
            recency = (i + 1) / max(1, rn)
            frags.append(Fragment(text=text, valence=rv, arousal=ra, weight=recency))

    return frags
