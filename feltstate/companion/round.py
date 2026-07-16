"""feltstate.companion.round — one end-to-end conversation turn.

Lifts the ``companion_turn`` flow from ``examples/with_llm.py`` into a reusable
library function, and adds the ``skip_tick`` / ``skip_history`` gates a real
companion needs for proactive and transient turns.

The order is the whole loop:

1. append the user message to ``history`` (unless ``skip_history``)
2. ``eng.tick(history)`` — independent estimate + integrate + persist
   (unless ``skip_tick``: a proactive/injected turn must not be *estimated* as if
   the agent's own words were the user's — see :mod:`feltstate.sources.base`)
3. ``eng.inject(user_text)`` — the felt block rides the front of the user turn
4. assemble ``[system, *prior, injected_user]`` and call the backend, sending
   only the most recent ``history_cap`` prior turns (an unbounded chat re-sent in
   full every turn is token cost, latency, and eventual context overflow)
5. append the reply to ``history`` (unless ``skip_history``), then trim
   ``history`` to the most recent ``history_cap`` turns in place
6. extract the first ``[tag]`` from the reply as the emotion label
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..engine import Engine
from .backend import LLMBackend

_TAG_RE = re.compile(r"\[([a-zA-Z_]+)\]")

# Roles that are real conversation turns replayed to the backend as-is. A stored
# turn under any other role (e.g. an internal proactive prompt) is kept for the
# record but never replayed as if the *user* had said it.
_REPLAY_ROLES = frozenset({"user", "assistant", "system"})


def _replayable(history: list[dict]) -> list[dict]:
    """Prior turns safe to send to the backend: drop internal-marker roles so an
    agent's own proactive prompt is never replayed as a user turn (see #51)."""
    return [m for m in history if m.get("role") in _REPLAY_ROLES]


def cap_history(history: list[dict], history_cap: int | None) -> None:
    """Trim ``history`` in place to its most recent ``history_cap`` turns.

    Bounds unbounded growth (memory + eventual context overflow). ``None`` or a
    non-positive cap means no trimming. Mutates the caller's list so every holder
    of the same reference sees the bound.
    """
    if history_cap is not None and history_cap > 0 and len(history) > history_cap:
        del history[:-history_cap]


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
    history_cap: int | None = None,
    record_role: str = "user",
) -> TurnResult:
    """Run one end-to-end round and return a :class:`TurnResult`.

    ``system_prompt`` is the static cached prefix (persona + reply rules) the
    caller owns; feltstate only rides the felt block on the user turn after it.
    Prior turns sit between the system message and the fresh (injected) user turn
    so multi-turn context is preserved while the cache prefix stays stable.

    ``history_cap`` bounds both what is *sent* to the backend and how large
    ``history`` is allowed to grow: only the most recent ``history_cap`` prior
    turns are replayed, and after the reply is recorded ``history`` is trimmed in
    place to that many turns. ``None`` = unbounded (legacy behaviour).

    ``record_role`` is the role under which ``user_text`` is stored in
    ``history``. It defaults to ``"user"``; a proactive/internal turn passes a
    non-user marker (e.g. ``"proactive"``) so the agent's own prompt is kept for
    the record but is **never** replayed to the backend as if the user had said
    it. The backend still sees this turn as a real ``user`` turn (the model needs
    something to answer); only the persisted copy carries the marker.
    """
    if not skip_history:
        history.append({"role": record_role, "content": user_text})

    if not skip_tick:
        eng.tick(history)

    injected = eng.inject(user_text)

    # prior = everything before this turn's user message (already cache-stable);
    # the freshest user turn is rebuilt from `injected` so the felt block rides it.
    # Drop internal-marker roles (so a past proactive prompt is never replayed as
    # a user turn) and send only the most recent `history_cap` prior turns.
    prior = _replayable(history[:-1] if not skip_history else history)
    if history_cap is not None and history_cap > 0:
        prior = prior[-history_cap:]
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        *prior,
        {"role": "user", "content": injected},
    ]

    reply = backend.complete(messages)

    if not skip_history:
        history.append({"role": "assistant", "content": reply})
        cap_history(history, history_cap)

    label = extract_emotion_tag(reply)
    if label is None and eng.state.mood.labels:
        label = eng.state.mood.labels[0]

    return TurnResult(reply=reply, emotion_label=label, felt_block=injected)
