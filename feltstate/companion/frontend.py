"""feltstate.companion.frontend — the avatar / "skin" seam.

feltstate emits mood **labels** (strings from :data:`~feltstate.config.DEFAULT_LABELS`,
or a pressure ``release_type`` such as ``"tears"`` / ``"burst_joy"``). A
:class:`FrontendAdapter` maps each label to whatever its own frontend speaks — a
Live2D expression index, a hotkey code, an animation name — and delivers it.

All mapping and throttling is the adapter's job. feltstate ships only the label;
it never owns an expression table (those are avatar-specific and stay in the
application layer), and it never grabs a screen itself.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class FrontendAdapter(ABC):
    """The skin seam: turn feltstate labels into native frontend signals."""

    @abstractmethod
    def label_to_token(self, label: str) -> Any | None:
        """Map a feltstate label (``"joyful"``, ``"sad"``, ``"tears"``,
        ``"burst_joy"``, ...) to a native expression token. Return ``None`` to
        skip an unknown label silently."""
        ...

    @abstractmethod
    async def push_expression(self, token: Any) -> bool:
        """Fire-and-forget deliver ``token`` to the frontend. Return ``True`` if
        delivered. Throttle here if the avatar needs a min switch interval."""
        ...

    def read_screen(self) -> bytes | None:
        """Return the latest frame bytes (e.g. JPEG) for a vision-in turn, or
        ``None`` for no eyes. The application decides *how* (file poll, capture);
        feltstate only asks. Default: no eyes."""
        return None


class NullFrontend(FrontendAdapter):
    """Text-only / Discord / tests: drops every expression, no eyes."""

    def label_to_token(self, label: str) -> Any | None:
        return None

    async def push_expression(self, token: Any) -> bool:
        return False
