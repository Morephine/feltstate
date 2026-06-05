"""feltstate.companion.presence — the "is the user here?" seam.

The scheduler must not initiate while the user is mid-conversation, and it must
back off for a while after they last spoke. How you *know* that is application-
specific (a WebSocket busy flag, a chat-history scan, an OS focus check), so the
scheduler depends only on this small interface, never on a transport or a file
format.

Both methods **fail open** when used through the gate helpers: a broken probe is
treated as "not busy" / "long idle" so the companion keeps living rather than
freezing on a down dependency.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class UserPresenceAdapter(ABC):
    """Tells the scheduler whether the user is around right now."""

    @abstractmethod
    def is_busy(self) -> bool:
        """Instant hard gate: is the user (or the agent) mid-turn right now —
        speaking, or audio still playing? ``True`` blocks any proactive fire."""
        ...

    @abstractmethod
    def seconds_since_last_user_message(self) -> float:
        """Seconds since the user last said something. ``float('inf')`` if they
        never have / it cannot be determined."""
        ...


class AlwaysIdlePresence(UserPresenceAdapter):
    """Demo / tests: the user is never busy and last spoke long ago — so every
    behaviour's user-idle gate passes."""

    def is_busy(self) -> bool:
        return False

    def seconds_since_last_user_message(self) -> float:
        return float("inf")
