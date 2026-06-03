"""feltstate.sources.keyword — a zero-dependency, rule-based AffectSource.

:class:`KeywordSource` scans the latest user message for emotion-bearing words
and maps them onto the package's discrete label vocabulary
(:data:`feltstate.config.DEFAULT_LABELS`), producing an
:class:`~feltstate.state.AffectDelta`. It needs nothing beyond the Python
standard library, so it runs out of the box — handy for tests, for a baseline,
and for environments where you can't reach a model endpoint.

**This is a coarse baseline, not the real thing.** A keyword scanner is a crude
proxy for affect: it can't read tone, sarcasm, context, or anything it has no
word for, and its valence numbers are hand-tuned guesses. For anything serious,
prefer :class:`~feltstate.sources.llm.LLMSource` (any OpenAI-compatible
endpoint) or your own fine-tuned classifier — both subclass the same
:class:`~feltstate.sources.base.AffectSource` interface, so swapping is a
one-line change.

Two design rules from :mod:`feltstate.sources.base` shape this implementation:

1. **Read the user, not the agent.** Only the latest *user* turn is scanned
   (via :func:`~feltstate.sources.base.latest_user_text`); the agent's own past
   replies are never fed back in, so it can't talk itself into a mood.
2. **React as the character, don't mirror the user.** The keyword table is
   deliberately written as *the character's reaction* to what was said, not as a
   transcription of the user's state. "thanks" lands as the character feeling
   ``grateful``/``tender``; an apology lands as a genuinely *mixed* feeling
   (a little down, a little reassured) rather than echoing the user's guilt.
   We don't paraphrase the user's content into the character's feeling — we map
   common cues to a plausible standing reaction and let the slow layers
   (traits, mood, relationship in :mod:`feltstate.affect`) integrate it.
"""
from __future__ import annotations

import re
from typing import Sequence

from ..config import DEFAULT_LABELS
from ..state import AffectDelta, AffectState
from .base import AffectSource, latest_user_text


# --------------------------------------------------------------------------- #
# Cue table                                                                   #
# --------------------------------------------------------------------------- #
# Each cue maps a set of surface phrases to the character's *reaction*:
#
#   "label":   one of feltstate.config.DEFAULT_LABELS (so it routes correctly
#              through LABEL_TO_PRESSURE / LABEL_TO_TRAITS downstream),
#   "valence": -1..+1 the reaction's pleasantness,
#   "arousal":  0..1 how activating it is,
#   "weight":   relative salience of this cue when several fire (used both to
#              rank labels and to scale confidence). Strong, unambiguous cues
#              ("thank you", "i hate") weigh more than soft ones ("hmm", "okay").
#
# Phrases are matched as whole words/short phrases, case-insensitively. This is
# intentionally small and general; extend it freely for your domain.
_CUES: tuple[dict, ...] = (
    # --- positive: gratitude / warmth ------------------------------------ #
    {"label": "grateful", "valence": 0.75, "arousal": 0.45, "weight": 1.0,
     "phrases": ("thank you", "thanks", "thank u", "thx", "ty",
                 "appreciate", "grateful", "means a lot")},
    {"label": "tender", "valence": 0.70, "arousal": 0.40, "weight": 1.0,
     "phrases": ("love you", "love u", "i love", "adore you", "you mean",
                 "care about you", "you're the best", "youre the best")},
    {"label": "proud", "valence": 0.80, "arousal": 0.55, "weight": 0.9,
     "phrases": ("proud of you", "so proud", "well done", "good job",
                 "nice work", "great work", "impressive")},
    {"label": "content", "valence": 0.55, "arousal": 0.30, "weight": 0.7,
     "phrases": ("happy", "glad", "pleased", "wonderful", "lovely",
                 "feels good", "feel good", "nice")},
    {"label": "joyful", "valence": 0.85, "arousal": 0.75, "weight": 1.0,
     "phrases": ("yay", "woohoo", "hooray", "amazing", "awesome",
                 "fantastic", "so happy", "delighted", "thrilled")},
    {"label": "excited", "valence": 0.75, "arousal": 0.85, "weight": 0.95,
     "phrases": ("excited", "can't wait", "cant wait", "so pumped",
                 "let's go", "lets go", "stoked")},
    {"label": "amused", "valence": 0.65, "arousal": 0.55, "weight": 0.7,
     "phrases": ("lol", "lmao", "haha", "hehe", "rofl", "funny", "so funny")},
    {"label": "relieved", "valence": 0.55, "arousal": 0.35, "weight": 0.8,
     "phrases": ("finally", "phew", "what a relief", "relieved",
                 "thank god", "at last")},
    {"label": "hopeful", "valence": 0.50, "arousal": 0.45, "weight": 0.7,
     "phrases": ("hopeful", "looking forward", "fingers crossed",
                 "i hope", "hope so", "optimistic")},
    {"label": "calm", "valence": 0.30, "arousal": 0.20, "weight": 0.6,
     "phrases": ("calm", "peaceful", "at ease", "relaxed", "no worries",
                 "it's fine", "its fine", "all good")},

    # --- curiosity / engagement ------------------------------------------ #
    {"label": "curious", "valence": 0.35, "arousal": 0.55, "weight": 0.7,
     "phrases": ("curious", "i wonder", "interesting", "intrigued",
                 "tell me more", "how does", "what if", "fascinating")},
    {"label": "focused", "valence": 0.15, "arousal": 0.55, "weight": 0.5,
     "phrases": ("let's work", "lets work", "let's get to work",
                 "focus", "let's start", "lets start", "let's build",
                 "lets build")},

    # --- negative: sadness / low energy ---------------------------------- #
    {"label": "sad", "valence": -0.70, "arousal": 0.35, "weight": 1.0,
     "phrases": ("sad", "unhappy", "crying", "i cried", "heartbroken",
                 "depressed", "miserable", "hurts", "this hurts",
                 "feel down", "feeling down", "down")},
    {"label": "lonely", "valence": -0.65, "arousal": 0.30, "weight": 0.95,
     "phrases": ("lonely", "alone", "no one", "nobody", "isolated",
                 "miss you", "i miss")},
    {"label": "tired", "valence": -0.35, "arousal": 0.15, "weight": 0.85,
     "phrases": ("tired", "exhausted", "so tired", "worn out", "burnt out",
                 "burned out", "sleepy", "drained", "no energy", "fed up")},
    {"label": "numb", "valence": -0.45, "arousal": 0.15, "weight": 0.7,
     "phrases": ("numb", "empty", "nothing matters", "don't care anymore",
                 "dont care anymore", "feel nothing")},
    {"label": "disappointed", "valence": -0.55, "arousal": 0.35, "weight": 0.9,
     "phrases": ("disappointed", "let down", "let me down", "bummed",
                 "such a shame", "what a shame", "too bad")},
    {"label": "wistful", "valence": -0.15, "arousal": 0.30, "weight": 0.7,
     "phrases": ("i miss", "miss the old", "wish things", "used to be",
                 "back then", "nostalgic", "bittersweet")},

    # --- negative: anxiety ----------------------------------------------- #
    {"label": "anxious", "valence": -0.55, "arousal": 0.75, "weight": 1.0,
     "phrases": ("anxious", "anxiety", "panic", "panicking", "freaking out",
                 "freaked out", "on edge", "dread")},
    {"label": "worried", "valence": -0.45, "arousal": 0.60, "weight": 0.9,
     "phrases": ("worried", "worry", "concerned", "nervous", "uneasy",
                 "what if it goes wrong")},
    {"label": "scared", "valence": -0.60, "arousal": 0.80, "weight": 1.0,
     "phrases": ("scared", "afraid", "terrified", "frightened", "fear")},
    {"label": "tense", "valence": -0.40, "arousal": 0.65, "weight": 0.8,
     "phrases": ("tense", "stressed", "stress", "so much pressure",
                 "under pressure", "overwhelmed")},
    {"label": "restless", "valence": -0.20, "arousal": 0.70, "weight": 0.6,
     "phrases": ("restless", "can't sit still", "cant sit still",
                 "jittery", "antsy")},

    # --- negative: anger / frustration ----------------------------------- #
    {"label": "frustrated", "valence": -0.55, "arousal": 0.70, "weight": 1.0,
     "phrases": ("ugh", "frustrated", "frustrating", "this is broken",
                 "broken", "doesn't work", "doesnt work", "not working",
                 "bug", "buggy", "crashed", "crash", "stuck", "argh", "grr")},
    {"label": "irritated", "valence": -0.45, "arousal": 0.60, "weight": 0.8,
     "phrases": ("irritated", "annoyed", "annoying", "bothered",
                 "fed up with", "sick of")},
    {"label": "angry", "valence": -0.75, "arousal": 0.85, "weight": 1.0,
     "phrases": ("angry", "furious", "i hate", "hate this", "pissed",
                 "pissed off", "mad", "outraged", "rage")},
    {"label": "indignant", "valence": -0.55, "arousal": 0.70, "weight": 0.8,
     "phrases": ("not fair", "unfair", "how dare", "ridiculous",
                 "unacceptable", "outrageous")},

    # --- mixed / social-cognitive ---------------------------------------- #
    # An apology is the canonical *mixed* cue: a little sting, a little
    # reassurance. We tag it sad-leaning but near-neutral and let mixed_blend
    # carry the ambivalence (see _build_delta).
    {"label": "wistful", "valence": -0.10, "arousal": 0.40, "weight": 0.85,
     "phrases": ("sorry", "i'm sorry", "im sorry", "my apologies",
                 "apologize", "apologise", "forgive me", "my bad")},
    {"label": "embarrassed", "valence": -0.35, "arousal": 0.55, "weight": 0.7,
     "phrases": ("embarrassed", "embarrassing", "so awkward", "ashamed",
                 "cringe", "mortified")},
    {"label": "surprised", "valence": 0.10, "arousal": 0.75, "weight": 0.7,
     "phrases": ("wow", "whoa", "what?!", "no way", "really?!",
                 "surprised", "didn't expect", "didnt expect", "unexpected")},
    {"label": "confused", "valence": -0.15, "arousal": 0.50, "weight": 0.6,
     "phrases": ("confused", "don't understand", "dont understand",
                 "doesn't make sense", "doesnt make sense", "lost", "huh")},
)

# Phrases an apology fires on — used to flag the mixed-feeling blend.
_APOLOGY_PHRASES = frozenset(
    p for c in _CUES if c["label"] == "wistful" for p in c["phrases"]
    if "sorry" in p or "apolog" in p or p in ("forgive me", "my bad")
)

# A single intensifier nudges arousal and confidence up a touch; a single
# negator/softener nudges them down. Cheap heuristics, not parsing.
_INTENSIFIERS = ("so ", "really ", "very ", "extremely ", "absolutely ",
                 "!!", "!!!", "super ", "incredibly ")
_SOFTENERS = ("a bit", "a little", "kind of", "kinda", "sort of", "slightly",
              "maybe", "i guess", "i think")


def _compile(phrases: Sequence[str]) -> re.Pattern:
    """Word-boundary, whole-phrase, case-insensitive matcher for a cue's phrases.

    Sorting by length first means a multi-word phrase ("thank you") is preferred
    over its substring ("thank"); ``\\b`` keeps "sad" from matching "salad".
    Phrases ending in punctuation (e.g. ``what?!``) skip the trailing boundary,
    which ``\\b`` would not satisfy.
    """
    parts = []
    for p in sorted(set(phrases), key=len, reverse=True):
        esc = re.escape(p)
        lead = r"\b" if p[:1].isalnum() else ""
        trail = r"\b" if p[-1:].isalnum() else ""
        parts.append(f"{lead}{esc}{trail}")
    return re.compile("|".join(parts), re.IGNORECASE)


# Pre-compile once at import; pairs each cue with its matcher.
_COMPILED: tuple[tuple[dict, re.Pattern], ...] = tuple(
    (cue, _compile(cue["phrases"])) for cue in _CUES
)

# Defensive: only ever emit labels the rest of the package understands.
_VALID_LABELS = frozenset(DEFAULT_LABELS)


class KeywordSource(AffectSource):
    """Rule-based :class:`~feltstate.sources.base.AffectSource` — no dependencies.

    Scans the latest user message against a small English cue table and emits an
    :class:`~feltstate.state.AffectDelta` whose ``labels`` are drawn from
    :data:`feltstate.config.DEFAULT_LABELS`. Cheap and deterministic; meant as a
    baseline and for tests. See the module docstring for why you'd want a real
    model instead.

    Parameters
    ----------
    neutral_confidence
        Confidence reported when nothing matches (low — "I'm not sure"). The
        downstream layers weight readings by confidence, so a near-zero value
        means an unrecognised message barely moves the state.
    max_labels
        How many of the strongest matched labels to keep (the schema allows 0-3).
    """

    def __init__(self, *, neutral_confidence: float = 0.15, max_labels: int = 3) -> None:
        self.neutral_confidence = float(neutral_confidence)
        self.max_labels = max(1, int(max_labels))

    # -- AffectSource ----------------------------------------------------- #
    def read(
        self,
        messages: Sequence[dict],
        *,
        baseline: AffectState,
        persona: str = "",
    ) -> AffectDelta:
        """Measure the character's reaction to the latest user turn.

        ``baseline`` and ``persona`` are accepted for interface parity but
        unused here: a keyword scanner has no way to ground its reading in the
        character's standing state (a real model-backed source does — that's a
        reason to prefer one). Returns a near-neutral, low-confidence delta when
        no cue fires.
        """
        text = latest_user_text(messages)
        if not text.strip():
            return AffectDelta(
                valence=0.0, arousal=0.4, labels=[],
                confidence=self.neutral_confidence,
            )

        lowered = text.lower()
        hits = self._scan(lowered)
        if not hits:
            return AffectDelta(
                valence=0.0, arousal=0.4, labels=["neutral"],
                confidence=self.neutral_confidence,
            )

        return self._build_delta(hits, lowered)

    # -- internals -------------------------------------------------------- #
    def _scan(self, lowered: str) -> list[dict]:
        """Return one accumulated hit dict per *label* that fired.

        If several phrases for the same label match, we keep one entry and let
        repeated matches inflate its ``weight`` (saying "tired, so tired,
        exhausted" should read as a stronger cue, not three labels).
        """
        by_label: dict[str, dict] = {}
        for cue, pat in _COMPILED:
            found = pat.findall(lowered)
            if not found:
                continue
            label = cue["label"]
            if label not in _VALID_LABELS:
                continue  # defensive — table only references DEFAULT_LABELS
            n = len(found)
            entry = by_label.get(label)
            if entry is None:
                by_label[label] = {
                    "label": label,
                    "valence": cue["valence"],
                    "arousal": cue["arousal"],
                    # diminishing returns on repeats: 1 hit -> w, 2 -> 1.5w, ...
                    "weight": cue["weight"] * (1.0 + 0.5 * (n - 1)),
                }
            else:
                entry["weight"] += cue["weight"] * (0.5 * n)
        return list(by_label.values())

    def _build_delta(self, hits: list[dict], lowered: str) -> AffectDelta:
        """Aggregate matched cues into a single AffectDelta.

        - ``valence``/``arousal``: weight-weighted mean of the matched cues, so a
          message with several agreeing cues lands harder, and a genuinely mixed
          message (e.g. "thanks but I'm exhausted") nets out in between rather
          than picking a side.
        - ``labels``: the top ``max_labels`` by weight.
        - ``confidence``: grows with total matched weight (more / stronger cues
          -> surer), tempered when the cues disagree in sign (ambivalence is
          less certain), and adjusted by intensifier / softener cues.
        - ``mixed_blend``: populated when the two strongest cues clash in sign
          (or an apology fired) so downstream rendering can show ambivalence.
        """
        hits.sort(key=lambda h: h["weight"], reverse=True)
        total_w = sum(h["weight"] for h in hits) or 1.0

        valence = sum(h["valence"] * h["weight"] for h in hits) / total_w
        arousal = sum(h["arousal"] * h["weight"] for h in hits) / total_w

        # Tone modifiers: cheap lexical nudges, capped so they can't dominate.
        boost = sum(1 for s in _INTENSIFIERS if s in lowered)
        damp = sum(1 for s in _SOFTENERS if s in lowered)
        arousal += 0.05 * min(boost, 3) - 0.05 * min(damp, 3)

        # Confidence: saturating function of accumulated weight, in [low, 0.85].
        # A keyword scanner should never claim near-certainty.
        base = self.neutral_confidence + (1.0 - self.neutral_confidence) * (
            1.0 - 1.0 / (1.0 + total_w)
        )
        confidence = base + 0.04 * min(boost, 3) - 0.05 * min(damp, 3)

        # Sign disagreement among the top cues => ambivalence => less certain.
        signs = {1 if h["valence"] > 0.05 else -1 if h["valence"] < -0.05 else 0
                 for h in hits}
        mixed = len({s for s in signs if s != 0}) > 1
        apology = any(p in lowered for p in _APOLOGY_PHRASES)
        if mixed:
            confidence *= 0.85

        labels = [h["label"] for h in hits[: self.max_labels]]

        mixed_blend = None
        if (mixed or apology) and len(hits) >= 1:
            primary = hits[0]
            # Prefer an opposite-sign runner-up as the secondary; for a lone
            # apology, pair its wistful sting with a reassuring counter-note.
            secondary = next(
                (h for h in hits[1:]
                 if (h["valence"] > 0) != (primary["valence"] > 0)),
                None,
            )
            sec_label = (
                secondary["label"] if secondary is not None
                else ("relieved" if apology and primary["valence"] <= 0 else None)
            )
            if sec_label is not None and sec_label in _VALID_LABELS:
                if secondary is not None:
                    # Two real cues that clashed in sign: share by weight.
                    p_score = primary["weight"] / total_w
                    s_score = secondary["weight"] / total_w
                else:
                    # Synthesized counter-note for a lone apology: the sting
                    # leads, the reassurance is a real-but-minor undertone.
                    p_score, s_score = 0.7, 0.3
                mixed_blend = {
                    "primary": primary["label"],
                    "secondary": sec_label,
                    "primary_score": round(p_score, 4),
                    "secondary_score": round(s_score, 4),
                }

        return AffectDelta(
            valence=round(max(-1.0, min(1.0, valence)), 4),
            arousal=round(max(0.0, min(1.0, arousal)), 4),
            labels=labels,
            confidence=round(max(0.0, min(0.85, confidence)), 4),
            mixed_blend=mixed_blend,
        )
