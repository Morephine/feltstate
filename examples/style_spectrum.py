#!/usr/bin/env python3
"""style_spectrum — turning state bands into delivery notes (an optional layer).

Run it::

    python examples/style_spectrum.py

The felt block describes *what the companion feels*. Most models mirror that
content — and then deliver it in the same flat cadence they deliver everything.
The gap is **delivery**: a thrilled line should read differently from a pressed
one before a single word of content changes.

This example is a reference *style renderer*: a pure function from
:class:`AffectState` to a few "delivery notes" — concrete, form-only
directives (sentence length, punctuation temperature, word-doubling, filler
words) that ride under the felt block. It is deliberately **not** part of the
engine: feltstate core describes and never instructs; this layer is an
app-side opt-in, and the docs page (``docs/STYLE_SPECTRUM.md``) spells out the
rules that keep it from flattening the character.

Deterministic: five prepared states, printed with their delivery notes.
The neutral state prints *nothing* — silence is the default here too.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from feltstate.state import AffectState


# --------------------------------------------------------------------------- #
# The style renderer — state bands in, form-only delivery notes out.          #
# --------------------------------------------------------------------------- #
def delivery_notes(state: AffectState, *, returning_after_gap: bool = False) -> list[str]:
    """Map the state to at most three form-only delivery directives.

    Rules enforced by construction (see docs/STYLE_SPECTRUM.md):
    form not content · off-neutral only · examples inline · hard cap of 3.
    """
    notes: list[str] = []
    m = state.mood
    p = state.pressure

    # A suppressed release dominates everything: held-in speech has one shape.
    if p.phase == "releasing" and (p.release_type or "").endswith("_suppress"):
        return [
            "keep sentences short; end lines a beat early, like words are being rationed",
            "no exclamation marks; strip filler words entirely",
        ]

    # An open release colours delivery by its flavour.
    if p.phase == "releasing":
        if p.release_type == "burst_joy":
            notes.append('short bursts, doubled words land well ("so so good"), exclamations free')
        elif p.release_type in ("tears", "collapse"):
            notes.append("let sentences trail; commas over full stops; no tidy endings")
        elif p.release_type == "anger":
            notes.append("clipped sentences; full stops hit hard; no softeners")

    # High-valence high-arousal: bright and quick.
    elif m.valence >= 0.4 and m.arousal >= 0.6:
        notes.append('quick, light sentences; an exclamation is fine ("that landed!")')
        if p.bars.joy >= 0.5:
            notes.append("doubled words read as sparkle, use sparingly")

    # Low-valence low-arousal: slow and spare.
    elif m.valence <= -0.25 and m.arousal <= 0.35:
        notes.append("slow it down: fewer clauses per sentence, soft closers, no rush to fill")

    # High-arousal negative (anxious edge): dense punctuation, short breath.
    elif m.valence <= -0.2 and m.arousal >= 0.6:
        notes.append("shorter breath: commas crowd in, sentences cut off earlier than usual")

    # Reunion after a real gap: open gently before anything else.
    if returning_after_gap and len(notes) < 3:
        notes.append("open gently; acknowledge the gap once, lightly — then move on")

    return notes[:3]


# --------------------------------------------------------------------------- #
# Five prepared states, printed with their notes.                             #
# --------------------------------------------------------------------------- #
def scenario(title: str, state: AffectState, **kw) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)
    notes = delivery_notes(state, **kw)
    if not notes:
        print("  (no delivery notes — neutral state, the default is silence)")
        return
    print("delivery:")
    for n in notes:
        print(f"  - {n}")


def main() -> None:
    # 1) joy, riding high
    s = AffectState()
    s.mood.valence, s.mood.arousal = 0.55, 0.7
    s.pressure.bars.joy = 0.6
    scenario("1. bright and quick — high valence, high arousal, joy lit", s)

    # 2) the same feeling, held in
    s = AffectState()
    s.mood.valence, s.mood.arousal = -0.3, 0.35
    s.pressure.phase = "releasing"
    s.pressure.release_type = "tears_suppress"
    scenario("2. pressed — a suppressed release owns the whole delivery", s)

    # 3) sad and slow
    s = AffectState()
    s.mood.valence, s.mood.arousal = -0.35, 0.25
    scenario("3. low and slow — negative valence, low arousal", s)

    # 4) anxious edge
    s = AffectState()
    s.mood.valence, s.mood.arousal = -0.3, 0.7
    scenario("4. anxious edge — negative valence, high arousal", s)

    # 5) neutral — the most important case: nothing
    s = AffectState()
    scenario("5. neutral — no notes at all", s)

    # 6) back after days
    s = AffectState()
    s.mood.valence, s.mood.arousal = 0.1, 0.4
    scenario("6. reunion — returning after a felt gap", s, returning_after_gap=True)

    print("\n" + "=" * 72)
    print("Form, never content: the notes shape punctuation, length and pace -")
    print("they never say what to feel. That stays the felt block's job.")
    print("=" * 72)


if __name__ == "__main__":
    main()
