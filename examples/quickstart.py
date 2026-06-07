#!/usr/bin/env python3
"""quickstart — watch a felt inner state build, spill over, and decay.

Run it directly, no setup, no network, no dependencies beyond the standard
library and feltstate itself::

    python examples/quickstart.py

What it demonstrates
--------------------
The three ideas the package is built on, made concrete in one short script:

* **Ground truth, not self-report.** Affect is *measured* every turn by a
  separate component — here the zero-dependency
  :class:`~feltstate.sources.keyword.KeywordSource` — not asked of a reply
  model. We feed in plain user messages and the agent gets a reaction it did
  not choose.
* **Tool, not controller.** The engine only produces *state* and renders it
  into a first-person block; it never emits an instruction like "be sad now".
  We print that block so you can see it is a feeling, not a command.
* **Identity-merge.** :meth:`Engine.render` returns the state as the agent's
  *own* felt experience, in plain English, and :meth:`Engine.inject` shows
  how that block rides on the front of the latest user turn (cache-safe — it
  stays out of the static system prompt).

It also shows the two things most memory layers miss:

* **Emotion accumulates** — a run of supportive messages fills the joy bar
  until it spills over into a visible release; a run of harsh ones fills a
  negative bar instead.
* **Emotion decays back toward neutral** — once the conversation goes quiet,
  ticking with an empty turn lets traits, mood, and the pressure bars ease
  home on their own. (Most stores decay *facts*; feltstate decays *feelings*.)

The script uses a brand-new temporary state file each run, so repeated runs
start from the same neutral baseline and stay reproducible.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from feltstate import Engine, KeywordSource, PersonaDials


# --------------------------------------------------------------------------- #
# Pretty-printing helpers (presentation only — not part of the library API).  #
# --------------------------------------------------------------------------- #
def banner(title: str) -> None:
    line = "=" * 72
    print(f"\n{line}\n{title}\n{line}")


def show_numbers(state) -> None:
    """A terse numeric peek at the state — the dashboard the *agent* never sees.

    We print raw numbers here only so you, the reader, can watch the dynamics
    move. The agent itself is handed the worded block from ``render()`` below,
    never these figures (that is the identity-merge discipline at work).
    """
    m, t, _rel, p = state.mood, state.traits, state.relationship, state.pressure
    bars = p.bars
    print(
        "  numbers | "
        f"mood v={m.valence:+.2f} a={m.arousal:.2f}  "
        f"traits dep={t.depression:.2f} opt={t.optimism:.2f} "
        f"anx={t.anxiety:.2f} cur={t.curiosity:.2f}"
    )
    print(
        "          | "
        f"bars sad={bars.sadness:.2f} ang={bars.anger:.2f} "
        f"anx={bars.anxiety:.2f} bnd={bars.boundary:.2f} joy={bars.joy:.2f}  "
        f"phase={p.phase}" + (f" ({p.release_type})" if p.release_type else "")
    )


def show_felt(eng: Engine) -> None:
    """Print the first-person felt block — exactly what the agent reads back."""
    print("  --- render() : the agent's own felt state ---")
    for ln in eng.render().splitlines():
        print("  | " + ln)


def turn(eng: Engine, user_text: str, *, history: list[dict]) -> None:
    """Run one real conversation turn and report the resulting state.

    Appends the user message to the running transcript, advances the engine one
    tick (which *measures*, integrates, and persists), then prints both the
    behind-the-scenes numbers and the worded felt block.
    """
    history.append({"role": "user", "content": user_text})
    print(f'\nuser: "{user_text}"')
    state = eng.tick(history)
    show_numbers(state)
    show_felt(eng)


def drive_to_release(eng: Engine, user_text: str, *, max_turns: int = 80) -> int:
    """Repeat one strong message until a pressure bar spills over (or we give up).

    The pressure cooker only *releases* once a bar crosses its threshold (0.85 by
    default), which takes a sustained run of same-flavoured readings — more turns
    than it's worth printing one-by-one. So we tick the same strong message in a
    tight loop, printing a compact per-turn line, and stop the moment the phase
    becomes ``releasing``. Returns the number of turns it took (0 if none).

    This is a real threshold crossing, not a staged one: the bar genuinely fills
    from the measured readings. We use it to surface the release-texture line,
    which the gentler main walkthrough doesn't run long enough to trigger.
    """
    convo: list[dict] = []
    for i in range(1, max_turns + 1):
        convo.append({"role": "user", "content": user_text})
        st = eng.tick(convo)
        bar, level = st.pressure.bars.max_bar()
        print(
            f"  turn {i:>2}: top bar {bar}={level:.2f}  phase={st.pressure.phase}"
            + (f" ({st.pressure.release_type})" if st.pressure.release_type else "")
        )
        if st.pressure.phase == "releasing":
            return i
    return 0


def quiet_tick(eng: Engine, n: int = 1) -> None:
    """Tick the engine with *no* user message ``n`` times — the decay path.

    Feeding an empty turn is the intended way to let the state cool between real
    messages: the source returns a low-confidence neutral reading, the trait and
    mood integrators apply only their pull back toward baseline, and the pressure
    bars shed a little each tick. On a timer, this is what eases a quiet
    conversation back to neutral. Note we pass an empty ``messages`` list, so the
    engine's "last time we really spoke" clock does **not** reset.
    """
    for _ in range(n):
        eng.tick([])


# --------------------------------------------------------------------------- #
# Main walkthrough                                                            #
# --------------------------------------------------------------------------- #
def main() -> None:
    # A fresh temp state file per run keeps the demo reproducible. In a real
    # app this would be a stable path so the agent's feelings persist.
    tmpdir = Path(tempfile.mkdtemp(prefix="feltstate_quickstart_"))
    state_path = tmpdir / "state.json"

    # PersonaDials describe how a character *expresses* what it feels — they
    # never change what is felt. This one is warm and fairly open, so its
    # releases lean toward showing feeling rather than holding it in. The
    # persona string is free text the caller owns; KeywordSource ignores it,
    # but a model-backed source (see with_llm.py) would fold it into its
    # measurement prompt. We keep it generic and character-agnostic here.
    dials = PersonaDials(warmth=0.75, vulnerability=0.65, emotional_explicitness=0.7)
    persona = (
        "A steady, attentive companion who works alongside the user. "
        "Reacts from its own feelings rather than mirroring the user's."
    )

    eng = Engine(
        source=KeywordSource(),  # ground-truth seam: rule-based, zero deps
        state_path=str(state_path),
        persona=persona,
        dials=dials,
    )

    banner("0) Starting state — fresh and neutral")
    show_numbers(eng.state)
    show_felt(eng)

    # -- Phase 1: a run of warm, positive messages. Watch the joy bar climb and
    #    optimism/closeness lift — emotion *accumulates* turn over turn. -------
    banner("1) A good stretch — positive readings accumulate")
    convo: list[dict] = []
    warm_messages = [
        "hey, good to see you!",
        "we finally shipped the thing, thank you so much for the help",
        "honestly this is amazing, I'm so happy right now",
        "haha yes, this is the best — really proud of what we built",
        "I appreciate you. this means a lot to me",
        "let's go!! I can't wait to show everyone",
    ]
    for msg in warm_messages:
        turn(eng, msg, history=convo)

    # -- Show the cache-safe injection. The felt block is a *dynamic prefix* on
    #    the latest user turn; the static persona/rules stay pinned at the top of
    #    the request (and stay cached). This printed string is what you'd send as
    #    the user-role content for this turn. ---------------------------------
    banner("2) inject() — felt block riding on the latest user message")
    next_user_message = "what should we build next?"
    injected = eng.inject(next_user_message)
    print("This is the *content of the user turn* you send to the reply model.")
    print("The persona/system prompt stays static above it (cached); only this")
    print("tail changes per turn — so the prompt cache keeps hitting.\n")
    for ln in injected.splitlines():
        print("  > " + ln)

    # -- Phase 3: the conversation goes quiet. No new user input, just time
    #    passing. Tick empty turns and watch the lift fade back toward neutral —
    #    feelings decay, the warmth doesn't last forever on its own. -----------
    banner("3) Silence — emotion decays back toward neutral")
    print("No user messages now; just ticking the engine as time passes.")
    print("(In production you'd tick this on a timer.)\n")
    for i in range(1, 7):
        quiet_tick(eng, n=3)
        print(f"-- after {i * 3} quiet ticks --")
        show_numbers(eng.state)
    print("\nThe felt block after the quiet stretch:")
    show_felt(eng)

    # -- Phase 4: a hard stretch. Negative readings fill a *different* bar.
    #    Pressure is multi-bar: sadness/anger/anxiety fill independently, and
    #    whichever crosses threshold first is what spills over (phase 6 drives one
    #    all the way over; here we just watch the negative bars take the lead). --
    banner("4) A hard stretch — negative bars take the lead instead of joy")
    rough = [
        "ugh, everything is broken again",
        "this is so frustrating, nothing works and I'm exhausted",
        "I'm really stressed, the whole thing crashed",
        "I hate this, it's just not working, I'm so fed up",
        "honestly I'm overwhelmed and burnt out",
        "argh, stuck again, this is hopeless",
    ]
    for msg in rough:
        turn(eng, msg, history=convo)

    # -- Phase 5: things settle and a repair happens. A warm, reconciling
    #    message after friction is read as genuinely *mixed* (a little sting, a
    #    little reassurance), and the slow layers integrate it. ----------------
    banner("5) Repair — a warm message after the rough patch")
    turn(eng, "sorry for the stress earlier. thank you for sticking with me", history=convo)
    turn(eng, "we're okay. I'm grateful for you, really", history=convo)

    # -- Phase 6: the pressure cooker actually spilling over. The gentle pacing
    #    above (a deliberately diffident KeywordSource, confidence ~0.15) keeps the
    #    bars climbing slowly and never quite crosses the release threshold — which
    #    is realistic, but means you never see a *release*. Here we use a more
    #    confident reading and a sustained run of one strong feeling to drive a bar
    #    over the line, so the "right now:" release-texture line appears. ---------
    banner("6) Pressure cooker — sustained feeling spills over into a release")
    # A separate engine + its own fresh state so this demo doesn't disturb the
    # narrative state above. Higher neutral_confidence => readings land harder.
    pc_path = tmpdir / "pressure_cooker.json"
    pc_eng = Engine(
        source=KeywordSource(neutral_confidence=0.5),
        state_path=str(pc_path),
        dials=dials,
    )
    print("Repeating one strongly joyful message until the joy bar crosses 0.85:\n")
    took = drive_to_release(pc_eng, "this is amazing, I'm so happy and proud, I love it!!")
    if took:
        print(f"\n  -> released after {took} turns. The felt block at release:")
        for ln in pc_eng.render().splitlines():
            print("  | " + ln)
        print(
            "\n  Note the 'right now:' line — that is the release *texture*, a felt\n"
            "  description of what's spilling over. It is still a feeling, never an\n"
            "  instruction: the agent reads it and decides for itself what to do."
        )
    else:
        print("  (no release within the turn budget — try raising max_turns)")

    banner("Done")
    print(f"State persisted to: {state_path}")
    print("Re-run any time — each run starts from a fresh neutral baseline.")
    print(
        "\nTakeaways:\n"
        "  * affect was MEASURED each turn (KeywordSource), never self-reported;\n"
        "  * it ACCUMULATED across a stretch, then DECAYED back toward neutral\n"
        "    once the conversation went quiet;\n"
        "  * a sustained feeling eventually SPILLED OVER into a release (phase 6);\n"
        "  * render() handed back a first-person FEELING, not a command;\n"
        "  * inject() put that feeling in the USER turn (cache-safe), leaving the\n"
        "    static persona/system prompt pinned and cached at the top."
    )


if __name__ == "__main__":
    main()
