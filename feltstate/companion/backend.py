"""feltstate.companion.backend — the reply-model seam.

Analogous to :class:`~feltstate.sources.base.AffectSource`, but for *generation*
rather than *measurement*. feltstate never owns the system prompt or the model
choice: a backend is handed an already-assembled ``messages`` array (the static
system prompt first, the feltstate-injected user turn last) and returns reply
text. Implementors wire it to an OpenAI-compatible HTTP endpoint, a Claude
daemon, a subprocess — anything.

Like :class:`AffectSource`, a backend must **never raise** on a transient
failure: return ``""`` and let the loop treat an empty reply as "no voice this
turn". That keeps a long-running companion alive across a flaky endpoint.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator


class LLMBackend(ABC):
    """The reply-model seam. Implement :meth:`complete` (and optionally
    :meth:`stream`) to give the companion a voice."""

    @abstractmethod
    def complete(self, messages: list[dict]) -> str:
        """Return the reply text for ``messages``.

        ``messages`` is a standard chat array — ``[{"role", "content"}, ...]`` —
        with the **static** system prompt first and the feltstate-injected user
        turn last. Must not raise on a transient failure; return ``""`` instead.
        """
        ...

    def stream(self, messages: list[dict]) -> Iterator[str]:
        """Yield reply text in chunks. Default: yield the whole :meth:`complete`
        result once. Override for token streaming with sentence-level TTS
        handoff."""
        yield self.complete(messages)


class EchoBackend(LLMBackend):
    """Zero-dependency reference backend: echoes the user's last line.

    Exercises the whole loop with no network and no model — for demos and tests.
    Not meant for real use.
    """

    def complete(self, messages: list[dict]) -> str:
        user = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"),
            "",
        )
        last = user.splitlines()[-1].strip() if user else ""
        return f"(echo) {last}"
