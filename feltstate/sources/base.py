"""feltstate.sources.base — the AffectSource interface.

An :class:`AffectSource` answers one question per turn:

    "Given what the user just said, and given who this character is and how it
     has been feeling, how does it feel *now*?"

It returns a measured :class:`~feltstate.state.AffectDelta`. This is the seam
where *ground truth, not self-report* lives: the source is a separate component
from whatever model generates the agent's replies. The reply-generating model
never decides how it feels — it only reads the felt state back afterwards.

The production system this was distilled from used a small fine-tuned model for
this. That model can't be shipped (it was trained on private data), so this
package ships the **interface** plus two reference sources you can run today:

* :class:`~feltstate.sources.keyword.KeywordSource` — zero-dependency, rule
  based. Runs out of the box, good for tests and a baseline.
* :class:`~feltstate.sources.llm.LLMSource` — points at any OpenAI-compatible
  endpoint (a local model or a hosted one) and asks it to *measure* affect.

Swap in your own (a fine-tuned classifier, a sentiment model, anything) by
subclassing :class:`AffectSource`.

Two design rules carried over from production, both about not fooling yourself:

1. **Read the user, not the agent's own words.** Feeding the agent's own past
   replies back in creates a self-reinforcing loop (it sounded sad, so it reads
   itself as sad, so it gets sadder). Sources should weight what was said *to*
   the character.
2. **React as the character, don't mirror the user.** If the user is excited
   about their project, the character doesn't become "excited about the user's
   project" — it has its own reaction (warmth, curiosity, fatigue) from its own
   standing baseline. Don't paraphrase the user's content into the character's
   feeling.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from ..state import AffectDelta, AffectState


class AffectSource(ABC):
    """Measures one :class:`AffectDelta` per turn from recent conversation."""

    @abstractmethod
    def read(
        self,
        messages: Sequence[dict],
        *,
        baseline: AffectState,
        persona: str = "",
    ) -> AffectDelta:
        """Measure how the character feels in reaction to the latest input.

        Parameters
        ----------
        messages
            Recent conversation as ``[{"role": "user"|"assistant", "content": str}, ...]``,
            oldest first. Implementations should weight ``user`` turns (see rule 1
            above) and may use the rest only for context.
        baseline
            The character's current :class:`AffectState` — its standing traits,
            mood and relationship. The reaction is grounded in *this*, so the
            same user message lands differently on a wary character than a
            trusting one.
        persona
            Optional short, free-text description of who the character is. Plain
            sources ignore it; model-backed sources fold it into their prompt.
            Keep persona out of code — it is the caller's to supply.

        Returns
        -------
        AffectDelta
            The measured reaction. Return a near-neutral delta (low ``confidence``)
            rather than raising when the signal is unclear.
        """
        raise NotImplementedError


def latest_user_text(messages: Sequence[dict]) -> str:
    """Helper: the most recent ``user`` message's content, or ``""``."""
    for m in reversed(list(messages)):
        if (m.get("role") or "") == "user":
            return str(m.get("content") or "")
    return ""
