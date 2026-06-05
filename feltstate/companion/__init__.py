"""feltstate.companion — assemble feltstate's parts into a runnable desktop pet.

The core ``feltstate`` package gives you the *parts* of a felt inner life
(measured affect, decaying mood, dreams, time sense). This subpackage is the
**orchestration + the seams** that turn those parts into a companion: a reply
backend, an avatar/skin adapter, a voice adapter, a presence probe, and a
proactive heartbeat that decides — on its own clock — when to speak, introspect,
dream, or write a diary.

Implement two adapters (a :class:`FrontendAdapter` skin + a :class:`VoiceAdapter`
voice), supply a persona and a :class:`CompanionConfig`, and :class:`Companion`
wires the rest. See ``examples/companion.py`` for an end-to-end run with stub
adapters — no network, no model, no third-party dependency.

Everything here is zero-dependency and contains no persona, path, or model
choice: those belong to the integrating application.
"""

from .app import Companion, CompanionConfig, run_companion
from .backend import EchoBackend, LLMBackend
from .backends_ref import OpenAICompatBackend
from .dispatch import BehaviorDispatcher
from .express import expression_signal
from .frontend import FrontendAdapter, NullFrontend
from .gates import SchedulerConfig
from .presence import AlwaysIdlePresence, UserPresenceAdapter
from .round import TurnResult, companion_turn, extract_emotion_tag
from .scheduler import BehaviorSource, CompanionScheduler
from .sources_ref import (
    BurstSource,
    DiarySource,
    DreamSource,
    FocusDurationSource,
    IntrospectSource,
    PendingTopicsSource,
    RandomSource,
    TimeWindowSource,
)
from .topics import JsonlTopicsStore, PendingTopicsStore
from .voice import NullVoice, VoiceAdapter

__all__ = [
    # facade
    "Companion",
    "CompanionConfig",
    "run_companion",
    # reply backend seam
    "LLMBackend",
    "EchoBackend",
    "OpenAICompatBackend",
    # one-turn orchestration
    "companion_turn",
    "TurnResult",
    "extract_emotion_tag",
    # frontend (skin) seam
    "FrontendAdapter",
    "NullFrontend",
    "expression_signal",
    # voice (TTS) seam
    "VoiceAdapter",
    "NullVoice",
    # presence seam
    "UserPresenceAdapter",
    "AlwaysIdlePresence",
    # dispatch seam
    "BehaviorDispatcher",
    # topics queue
    "PendingTopicsStore",
    "JsonlTopicsStore",
    # scheduler + behaviours
    "CompanionScheduler",
    "BehaviorSource",
    "SchedulerConfig",
    "PendingTopicsSource",
    "TimeWindowSource",
    "FocusDurationSource",
    "RandomSource",
    "BurstSource",
    "IntrospectSource",
    "DreamSource",
    "DiarySource",
]
