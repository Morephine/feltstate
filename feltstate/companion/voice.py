"""feltstate.companion.voice — the TTS / voice-coloring seam.

A :class:`VoiceAdapter` turns reply text into speech. It receives the text
(already stripped of any ``[tag]`` / control markers by the caller) plus an
``emotion_hint`` — a raw feltstate label like ``"sad"`` or ``"joyful"`` — and
maps that hint to a reference clip / SSML style *internally*. feltstate ships
only the label; the voice mapping (ref-audio paths, SSML) stays in the adapter.

Like the other seams, :meth:`synthesize` must not raise on a transient failure —
return ``None`` so the loop survives a flaky TTS backend.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

# Anything left after stripping whitespace + common CJK/Latin punctuation means
# there is something worth speaking. Mirrors the production TTS empty-check gate.
_NON_SPEAKABLE = re.compile(r"[\s.,!?;:~…\-—'\"()\[\]，。！？；：、''\"\"『』「」（）【】]+")


class VoiceAdapter(ABC):
    """The TTS seam: synthesize speech, colored by an emotion hint."""

    @abstractmethod
    async def synthesize(self, text: str, emotion_hint: str = "") -> str | None:
        """Return a path to a synthesized audio file, or ``None`` if there is
        nothing to say / the backend is unavailable.

        ``text`` is already stripped of ``[tag]`` markers. ``emotion_hint`` is a
        raw feltstate label (``"sad"``, ``"joyful"``, ``""`` / ``"neutral"`` =
        default voice). The **caller** owns cleanup of the returned file (mirrors
        the production ``TTSInterface``: synthesize, send, then delete). Must not
        raise on a transient failure — return ``None``.
        """
        ...

    def should_speak(self, text: str) -> bool:
        """Whether ``text`` has anything worth speaking (skips pure punctuation /
        whitespace). Default heuristic; override freely."""
        return bool(_NON_SPEAKABLE.sub("", text or ""))


class NullVoice(VoiceAdapter):
    """Text-only / tests: never synthesizes."""

    async def synthesize(self, text: str, emotion_hint: str = "") -> str | None:
        return None
