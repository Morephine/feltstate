"""feltstate.companion.round — one end-to-end conversation turn.

Lifts the ``companion_turn`` flow from ``examples/with_llm.py`` into a reusable
library function, and adds the ``skip_tick`` / ``skip_history`` gates a real
companion needs for proactive and transient turns.

The order is the whole loop:

1. append the user message to ``history`` (unless ``skip_history``)
2. ``eng.tick(history)`` — ground-truth measure + integrate + persist
   (unless ``skip_tick``: a proactive/injected turn must not be *measured* as if
   the agent's own words were the user's — see :mod:`feltstate.sources.base`)
3. ``eng.inject(user_text)`` — the felt block rides the front of the user turn
4. assemble ``[system, *prior, injected_user]`` and call the backend
5. append the reply to ``history`` (unless ``skip_history``)
6. extract the first ``[tag]`` from the reply as the emotion label
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..engine import Engine
from .backend import LLMBackend

_TAG_RE = re.compile(r"\[([a-zA-Z_]+)\]")


@dataclass
class TurnResult:
    """What one turn produced — enough to drive a skin + voice + logging."""

    reply: str
    emotion_label: str | None  # first [tag] in the reply, else mood.labels[0]
    felt_block: str  # the injected user turn the reply model actually saw


def extract_emotion_tag(reply_text: str) -> str | None:
    """Return the first ``[label]`` tag in ``reply_text`` (lowercased), or
    ``None``. Pure utility — never auto-wired into the Engine (tool, not
    controller); the caller decides what to do with it."""
    m = _TAG_RE.search(reply_text or "")
    return m.group(1).lower() if m else None


def companion_turn(
    eng: Engine,
    backend: LLMBackend,
    history: list[dict],
    user_text: str,
    *,
    system_prompt: str,
    skip_tick: bool = False,
    skip_history: bool = False,
) -> TurnResult:
    """Run one end-to-end round and return a :class:`TurnResult`.

    ``system_prompt`` is the static cached prefix (persona + reply rules) the
    caller owns; feltstate only rides the felt block on the user turn after it.
    Prior turns sit between the system message and the fresh (injected) user turn
    so multi-turn context is preserved while the cache prefix stays stable.
    """
    if not skip_history:
        history.append({"role": "user", "content": user_text})

    if not skip_tick:
        eng.tick(history)

    injected = eng.inject(user_text)

    # prior = everything before this turn's user message (already cache-stable);
    # the freshest user turn is rebuilt from `injected` so the felt block rides it.
    prior = history[:-1] if not skip_history else list(history)
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        *prior,
        {"role": "user", "content": injected},
    ]

    reply = backend.complete(messages)

    if not skip_history:
        history.append({"role": "assistant", "content": reply})

    label = extract_emotion_tag(reply)
    if label is None and eng.state.mood.labels:
        label = eng.state.mood.labels[0]

    return TurnResult(reply=reply, emotion_label=label, felt_block=injected)
