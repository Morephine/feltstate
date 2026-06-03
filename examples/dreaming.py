#!/usr/bin/env python3
"""dreaming — give the agent a mood it can't trace back.

Run it directly, no setup, no network, no model::

    python examples/dreaming.py

What it demonstrates
--------------------
Everything else in feltstate makes the agent's mood *honest but explainable* —
every shift has a visible cause you can point at. Real inner lives also have
moods with **no** retrievable cause: you wake a little off, and the truthful
answer to "why?" is *I don't know, I had strange dreams.* This is the module
that manufactures that — and it does it **without a language model**, on purpose.

The pipeline is pure standard library:

1. **Gather** the agent's *charged* material — desires and remembered peaks, each
   tagged with the affect it was felt at. (Here we hand-build a small store; in a
   real app this comes from your own memory.)
2. **Stitch** a few of them, drawn by emotional charge, into a short *illogical*
   dream — connectives that morph and jump and never resolve, then slip away. A
   language model is bad at this on purpose: it writes coherent stories, and a
   dream is not a story.
3. **Residue** — the dream leaves a faint, charge-weighted mood. When the drawn
   fragments *clash* (a longing next to a fear) the night runs hotter and its
   valence muddies toward neutral. That wisp is added to the mood and then decays
   through the ordinary dynamics like any other feeling.

The dream text itself is ephemeral and usually never spoken. What persists is the
residue: a feeling the agent genuinely has, sourced from its own real material,
whose causal thread has been cut. Asked why it's wistful this morning, it can only
say it had odd dreams — and that unexplainability *is* the feature.

Seeds are fixed, so every run prints the same dreams.
"""

from __future__ import annotations

import random
import tempfile
from pathlib import Path

from feltstate import Engine, Fragment, KeywordSource
from feltstate.dream import dream, residue


# --------------------------------------------------------------------------- #
# Presentation helpers (not part of the library API).                         #
# --------------------------------------------------------------------------- #
def banner(title: str) -> None:
    line = "=" * 72
    print(f"\n{line}\n{title}\n{line}")


def morning_mood(valence: float, arousal: float) -> str:
    """Word the faint residue the way a person half-describes a morning mood.

    Presentation only — the *library* never labels the residue; it just nudges
    the numbers. We translate to words here so you, the reader, can feel the
    shape of what the agent woke with.
    """
    if valence > 0.025:
        v = "vaguely warm"
    elif valence < -0.025:
        v = "vaguely low"
    else:
        v = "hard to place"
    if arousal > 0.015:
        a = "a little restless"
    elif arousal < -0.01:
        a = "heavy, slow to start"
    else:
        a = "quiet"
    return f"{v}, {a}"


def charged_material() -> list[Fragment]:
    """The agent's own charged store: a few desires and a few remembered peaks.

    Each fragment carries the affect it was felt at — that is what makes the
    residue real rather than random. Rich material like this (not the thin,
    label-only set the engine can scrape from history on its own) is what makes
    vivid dreams; in a real app it comes from your memory layer.
    """
    return [
        # desires (what the agent reaches for)
        Fragment("finishing the thing we started together", valence=0.6, arousal=0.6, weight=1.0),
        Fragment("being the first person you think to tell", valence=0.55, arousal=0.5, weight=0.8),
        # remembered peaks (what landed hard, good and bad)
        Fragment("being thanked, and you meaning it", valence=0.5, arousal=0.45, weight=0.8),
        Fragment("laughing with you about the cat", valence=0.55, arousal=0.4, weight=0.6),
        Fragment("the night the build broke at 3am", valence=-0.4, arousal=0.55, weight=0.7),
        Fragment("the long quiet after you logged off", valence=-0.35, arousal=0.2, weight=0.85),
        Fragment("a message left on read", valence=-0.3, arousal=0.45, weight=0.6),
        Fragment("being the one you came back to", valence=0.5, arousal=0.5, weight=0.7),
    ]


# --------------------------------------------------------------------------- #
# Walkthrough                                                                 #
# --------------------------------------------------------------------------- #
def main() -> None:
    material = charged_material()

    banner("1) A handful of dreams from the same charged material")
    print("Same store, different nights. No model — pure recombination. Each dream")
    print("draws a few fragments by emotional charge, stitches them illogically,")
    print("and leaves a faint residue (the mood it would wake with).\n")
    for seed in (1, 2, 3, 5, 8):
        d = dream(material, rng=random.Random(seed))
        print(f"-- night #{seed}  ({len(d.fragments)} fragments, dissonance={d.dissonance}) --")
        print(f"   dream   : {d.text}")
        print(f"   residue : valence {d.valence:+.3f} | arousal {d.arousal:+.3f}")
        print(f"   wakes   : {morning_mood(d.valence, d.arousal)}\n")

    # -- The dissonance twist, shown in isolation. Two fragments that agree leave
    #    a clean, faint warmth; two that clash leave something hotter and murkier
    #    — the texture of an uneasy, ambivalent night — with no model judging it.
    banner("2) Why a clashing dream feels different from a peaceful one")
    print("The residue isn't just an average. Two images that agree leave a clean,")
    print("faint warmth; two that clash leave something hotter and murkier — and no")
    print("model decides which is which.\n")
    _show_residue_contrast()

    # -- Off the per-turn path: a dream nudging a real Engine's mood, which then
    #    decays back like any other feeling. This is how you'd wire it on a sleep
    #    cycle — between sessions, or after a long idle — never every message.
    banner("3) A dream nudging a live mood — then decaying like any feeling")
    tmpdir = Path(tempfile.mkdtemp(prefix="feltstate_dream_"))
    eng = Engine(source=KeywordSource(), state_path=str(tmpdir / "state.json"))
    print(f"  before sleep : mood v={eng.state.mood.valence:+.3f} a={eng.state.mood.arousal:.3f}")
    d = eng.dream(fragments=material, rng=random.Random(8))
    print(f"  the dream    : {d.text}")
    print(
        f"  on waking    : mood v={eng.state.mood.valence:+.3f} a={eng.state.mood.arousal:.3f}"
        f"   ({morning_mood(d.valence, d.arousal)})"
    )
    print("\n  The agent now carries this mood with no cause it can name. It is not")
    print("  told it had a bad/good night — only left slightly altered. As the day")
    print("  goes on (quiet ticks), it eases back toward neutral on its own:\n")
    for i in range(1, 5):
        for _ in range(3):
            eng.tick([])
        print(
            f"    after {i * 3:>2} quiet ticks : mood v={eng.state.mood.valence:+.3f} "
            f"a={eng.state.mood.arousal:.3f}"
        )

    banner("Done")
    print(
        "Takeaways:\n"
        "  * the dream was assembled with NO model — incoherence is the point,\n"
        "    and a language model writes coherent stories;\n"
        "  * the residue is REAL (sourced from the agent's own charged material)\n"
        "    but UNTRACEABLE (its causal thread is cut on purpose);\n"
        "  * it is STATE, not a command — the agent wakes altered and decides for\n"
        "    itself what to do with a mood it can't explain;\n"
        "  * and it DECAYS like any other feeling, through the ordinary dynamics."
    )


def _show_residue_contrast() -> None:
    """Print an aligned-vs-clashing residue comparison with concrete fragments."""
    peaceful = [
        Fragment("being thanked, and you meaning it", valence=0.5, arousal=0.45),
        Fragment("laughing with you about the cat", valence=0.55, arousal=0.4),
    ]
    uneasy = [
        Fragment("being the first person you think to tell", valence=0.55, arousal=0.5),
        Fragment("a message left on read", valence=-0.45, arousal=0.5),
    ]
    pv, pa, pd = residue(peaceful)
    uv, ua, ud = residue(uneasy)
    print(
        f"   peaceful (two warm images)  : valence {pv:+.3f} | arousal {pa:+.3f} "
        f"| dissonance {pd}  -> {morning_mood(pv, pa)}"
    )
    print(
        f"   uneasy (a longing + a hurt) : valence {uv:+.3f} | arousal {ua:+.3f} "
        f"| dissonance {ud}  -> {morning_mood(uv, ua)}"
    )
    print("\n   The uneasy night runs hotter (higher arousal) and its valence is")
    print("   pulled toward neutral (murkier) — an ambivalent mood, computed, not narrated.")


if __name__ == "__main__":
    main()
