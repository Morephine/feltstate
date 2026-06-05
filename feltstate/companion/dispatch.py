"""feltstate.companion.dispatch — the "say it" seam.

When the scheduler decides a behaviour should fire, it hands the payload to a
:class:`BehaviorDispatcher`. The dispatcher is where the application wires the
behaviour to its own pipeline: route a proactive line through
:func:`~feltstate.companion.round.companion_turn` (with ``skip_tick=True``), run
a silent introspection, write a diary entry, push a topic. feltstate does not
know what endpoints exist — it only knows *when* and *what kind*.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BehaviorDispatcher(ABC):
    """Carries a scheduled behaviour out to the application's voice/side-effects."""

    @abstractmethod
    def dispatch(self, kind: str, payload: str) -> bool:
        """Act on a fired behaviour. ``kind`` is the behaviour source's kind
        (``"pending"`` / ``"time_window"`` / ``"random"`` / ``"introspect"`` /
        ``"dream"`` / ``"diary"`` / ...); ``payload`` is the source's output
        string (a line to speak, a prompt, or ``""`` for a silent kind). Return
        ``True`` if handled. Should not raise — a failure here must not kill the
        heartbeat thread; log and return ``False``."""
        ...
