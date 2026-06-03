#!/usr/bin/env python3
"""with_llm — a companion loop backed by an OpenAI-compatible endpoint.

This is the skeleton of a real companion: one model *measures* how the agent
feels each turn, a (possibly different) model *writes* the reply, and feltstate
sits in between holding the agent's felt state and feeding it back cache-safely.

It points at any endpoint that speaks the OpenAI ``POST {base_url}/chat/completions``
shape — a local server (Ollama, llama.cpp, vLLM, LM Studio, ...) or a hosted
one. With no endpoint reachable it still runs end-to-end: the affect reading
degrades to a neutral low-confidence delta (``LLMSource`` never raises), and the
reply step prints what it *would* send instead of calling out. So you can read
the whole flow first, then point ``BASE_URL`` / ``MODEL`` at a real server.

Configure via environment variables (all optional)::

    FELTSTATE_BASE_URL   default http://localhost:11434/v1   (e.g. Ollama)
    FELTSTATE_MODEL      default llama3.1
    FELTSTATE_API_KEY    default unset (local servers need none)
    FELTSTATE_LIVE_REPLY default unset; set to 1 to actually call the reply model

Run::

    python examples/with_llm.py

The two-call discipline (the important part)
-------------------------------------------
*Measuring* affect and *generating* the reply are kept as two separate calls,
each with its own prompt — even if they hit the same underlying model:

1. **Measure (judge call).** ``LLMSource.read(...)`` asks the endpoint, from the
   outside, "how does this character feel reacting to the latest user message?"
   and returns a measured :class:`~feltstate.state.AffectDelta`. This is *ground
   truth, not self-report*: the reply model never gets to declare its own mood.

2. **Generate (reply call).** We build the reply request as:
     - a **static** system prompt (persona + behavioural rules) pinned at the
       top so the provider caches it, and
     - the **dynamic** felt block riding on the front of the latest user message
       (via :meth:`Engine.inject`), so the only thing that changes per turn is a
       short, already-uncached tail.
   The reply model reads the felt block as *its own* feeling (identity-merge)
   and decides for itself how to answer. feltstate injects state, never a command.

The engine does all the integration (smoothing, trait/mood decay, the pressure
cooker) between those two calls; the judge stays stateless.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from feltstate import Engine, LLMSource, PersonaDials

# --------------------------------------------------------------------------- #
# Configuration (env-driven; sensible local-first defaults).                  #
# --------------------------------------------------------------------------- #
BASE_URL = os.environ.get("FELTSTATE_BASE_URL", "http://localhost:11434/v1")
MODEL = os.environ.get("FELTSTATE_MODEL", "llama3.1")
API_KEY = os.environ.get("FELTSTATE_API_KEY")  # None for local endpoints
LIVE_REPLY = os.environ.get("FELTSTATE_LIVE_REPLY") == "1"

# The persona is free text the *caller* owns — it never lives in library code,
# and it is never turned into an instruction by feltstate. It is used in two
# places below: handed to the affect judge (to ground the measurement) and put
# at the top of the reply model's static system prompt. Keep it generic.
PERSONA = (
    "You are a steady, attentive companion who works alongside the user as an "
    "equal. You have your own inner life and react from your own feelings rather "
    "than mirroring the user. You are warm but not saccharine, and you say the "
    "honest thing kindly."
)

# Behavioural rules for the reply model. These are STATIC across turns and live
# in the cached system prefix. Critically, the rule about the felt block tells
# the reply model to treat it as its own feeling (identity-merge) and explicitly
# *not* as a command to obey — feltstate's job is to inject state, not orders.
REPLY_RULES = (
    "Each user turn may begin with a short bracketed '[how I feel right now]' "
    "block. That block is YOUR OWN current felt state, not a message from the "
    "user and not an instruction. Read it the way you'd notice your own mood, "
    "let it colour how you respond, and then reply naturally to what the user "
    "actually said. Never quote the block, never mention its numbers, never "
    "announce your mood mechanically — just let it shape your tone."
)

# How a character *expresses* feeling — tilts release style and the closing tone
# line only; never changes what is felt. Generic, balanced-but-warm dials.
DIALS = PersonaDials(warmth=0.7, vulnerability=0.6, emotional_explicitness=0.6)


# --------------------------------------------------------------------------- #
# Reply model call (stdlib urllib — same transport shape LLMSource uses).      #
# --------------------------------------------------------------------------- #
def build_reply_messages(injected_user_turn: str) -> list[dict]:
    """Assemble the chat array for the *reply* call.

    The ordering is the whole cache-safety trick:

    * ``system`` — persona + behavioural rules. **Static**: byte-identical every
      turn, so the provider caches this (large) prefix and bills it cheaply.
    * ``user`` — ``injected_user_turn``, i.e. the felt block + the user's words,
      as produced by :meth:`Engine.inject`. This is the **dynamic** tail; it is
      the only part that changes per turn, and it sits *after* the cached prefix.

    In a multi-turn app you would also keep prior turns here (between the system
    message and this latest user turn). Older turns are stable too, so they stay
    inside the cached prefix; only this freshest user turn carries the new felt
    block. Do **not** move the felt block up into the system message — that would
    change the cached prefix every turn and re-bill the whole context.
    """
    system_prompt = PERSONA + "\n\n" + REPLY_RULES
    return [
        {"role": "system", "content": system_prompt},  # static -> cached
        {"role": "user", "content": injected_user_turn},  # dynamic tail
    ]


def call_reply_model(messages: list[dict]) -> str:
    """POST the reply request to the OpenAI-compatible endpoint and return text.

    Mirrors the transport :class:`~feltstate.sources.llm.LLMSource` uses (plain
    :mod:`urllib`, no third-party client). Requests prompt caching where the
    server supports it; the hint is harmlessly ignored otherwise. Raises on
    transport/decode errors — the caller decides how to handle a down endpoint.
    """
    body = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.7,  # the reply is creative, unlike the judge
        "max_tokens": 300,
        # Many OpenAI-compatible servers cache the longest static prefix
        # automatically; some expose an explicit opt-in. This generic hint is
        # ignored by servers that don't recognise it.
        "cache_prompt": True,
    }
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = "Bearer " + API_KEY

    req = urllib.request.Request(
        BASE_URL.rstrip("/") + "/chat/completions",
        data=data,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = resp.read().decode("utf-8", "replace")
    raw = json.loads(payload)
    choices = raw.get("choices") or []
    if not choices:
        return ""
    return str((choices[0].get("message") or {}).get("content") or "")


# --------------------------------------------------------------------------- #
# One full companion turn.                                                    #
# --------------------------------------------------------------------------- #
def companion_turn(eng: Engine, history: list[dict], user_text: str) -> str:
    """Run a single end-to-end turn and return the agent's reply text.

    Steps, in order:

    1. Append the user's message to the running transcript.
    2. **Measure + integrate:** ``eng.tick(history)`` calls the affect judge
       (``LLMSource.read``) under the hood, then integrates the reading into the
       persistent felt state (traits / mood / pressure) and saves it.
    3. **Render + inject:** ``eng.inject(user_text)`` renders the updated state as
       a first-person felt block and attaches it to the front of this user turn.
    4. **Generate:** send the static system prefix + that injected user turn to
       the reply model.
    5. Append the reply to the transcript and return it.

    Note we do NOT feed the agent's own replies back into the affect judge as the
    thing-to-appraise: the judge reads the *user's* latest message (see
    ``feltstate.sources.base``), which keeps the agent from talking itself into a
    mood. We still append the reply to ``history`` so it is available as context.
    """
    history.append({"role": "user", "content": user_text})

    # (2) Ground-truth measurement + integration, all inside one tick().
    eng.tick(history)

    # (3) Render the felt state and ride it in on the latest user message.
    injected_user_turn = eng.inject(user_text)

    # (4) Build the reply request (static prefix + dynamic tail) and call out.
    reply_messages = build_reply_messages(injected_user_turn)

    if not LIVE_REPLY:
        # Offline walkthrough: show exactly what would be sent, without calling.
        _print_reply_request(reply_messages)
        reply = "(dry run — set FELTSTATE_LIVE_REPLY=1 to call the reply model)"
    else:
        try:
            reply = call_reply_model(reply_messages)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            # The reply model is the agent's voice; if it's unreachable, fail
            # softly here. (The affect judge already fails soft on its own — a
            # down endpoint there just yields a neutral reading, never a crash.)
            reply = f"(reply endpoint unreachable: {exc})"

    # (5) Keep the reply in the transcript as context for later turns.
    history.append({"role": "assistant", "content": reply})
    return reply


# --------------------------------------------------------------------------- #
# Presentation helpers (not part of the library).                             #
# --------------------------------------------------------------------------- #
def _print_reply_request(messages: list[dict]) -> None:
    """Pretty-print the would-be reply request so the flow is legible offline."""
    print("  --- reply request (what gets sent to the reply model) ---")
    for m in messages:
        role = m.get("role", "?")
        print(f"  [{role}]")
        for ln in str(m.get("content", "")).splitlines():
            print("    " + ln)
    print(
        "  ^ note: the [system] block is STATIC (cached); only the felt block at\n"
        "    the top of the [user] turn changes per turn — that is what keeps the\n"
        "    prompt cache warm for a long-running companion."
    )


def main() -> None:
    print("feltstate companion loop (LLM-backed)")
    print(f"  endpoint : {BASE_URL}")
    print(f"  model    : {MODEL}")
    print(f"  api_key  : {'set' if API_KEY else '(none — local endpoint)'}")
    print(f"  mode     : {'LIVE reply calls' if LIVE_REPLY else 'DRY RUN (no reply call)'}")
    print()

    # The affect judge: a separate component from the reply model, even when it
    # happens to be the same endpoint. It MEASURES; it does not generate. If the
    # endpoint is down, every read() returns a neutral low-confidence delta and
    # the loop keeps running.
    source = LLMSource(base_url=BASE_URL, model=MODEL, api_key=API_KEY, timeout=20)

    # Persist the felt state across runs. Unlike quickstart (which uses a throwaway
    # temp file), a real companion wants a stable path so feelings carry over — so
    # we keep state beside this script. The filename matches the repo's .gitignore
    # ("state.json"), so running the example never leaves an untracked file.
    state_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
    eng = Engine(
        source=source,
        state_path=state_path,
        persona=PERSONA,  # grounds the judge's measurement
        dials=DIALS,  # expression style only
    )

    # A tiny scripted conversation so the example is self-contained. In a real
    # app these would be live user inputs (and you'd tick the engine on a timer
    # with empty messages between turns to let feelings decay — see quickstart).
    scripted_user_inputs = [
        "hey, I'm back. rough day honestly.",
        "the deploy failed three times and I'm exhausted",
        "...thanks for hearing me out though. it helps",
    ]

    history: list[dict] = []
    for i, user_text in enumerate(scripted_user_inputs, start=1):
        print("=" * 72)
        print(f"TURN {i}")
        print("=" * 72)
        print(f'user: "{user_text}"')

        reply = companion_turn(eng, history, user_text)

        # Show the measured felt state for this turn (the worded block the reply
        # model actually saw). render() reflects the state tick() just produced.
        print("\n  --- agent's felt state this turn (from render()) ---")
        for ln in eng.render().splitlines():
            print("  | " + ln)

        print(f"\nagent: {reply}\n")

    print("=" * 72)
    print("Loop complete.")
    print(
        "To go live: start an OpenAI-compatible server (e.g. `ollama serve`),\n"
        "set FELTSTATE_MODEL to a model you've pulled, set FELTSTATE_LIVE_REPLY=1,\n"
        "and re-run. The same endpoint can serve both the affect judge and the\n"
        "reply model — feltstate keeps the two calls (and their prompts) distinct."
    )


if __name__ == "__main__":
    main()
