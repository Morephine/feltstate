"""feltstate.companion.app — the one-handle facade.

``CompanionConfig`` + ``Companion`` + ``run_companion``: wire an
:class:`~feltstate.engine.Engine`, a reply backend, a frontend skin, a voice,
and a presence probe into a single object. :meth:`Companion.say` runs a
foreground user turn (feel → reply → express → speak); :meth:`Companion.start`
runs the heartbeat that makes the companion act on its own.

This is the minimal-wiring layer. Everything pluggable is an adapter; nothing
here holds a persona, a path, or a model choice — those come from the caller
via :class:`CompanionConfig` and the adapters.

Concurrency note: the foreground :meth:`say` and the background heartbeat both
touch the engine. A :class:`threading.RLock` (``self._lock``) serialises paths
across OS threads, while a per-event-loop :class:`asyncio.Lock` serialises sibling
coroutines. Together they protect ``engine.state``, ``engine.imprints``, adapter
side effects, and ``self.history`` from interleaving.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import re
import threading
import weakref
from dataclasses import dataclass, field
from pathlib import Path

from ..config import DEFAULT_CONFIG, Config, PersonaDials
from ..engine import Engine
from ..sources.base import AffectSource
from ..state import AffectState
from .backend import LLMBackend
from .dispatch import BehaviorDispatcher
from .express import expression_signal
from .frontend import FrontendAdapter
from .gates import SchedulerConfig
from .presence import UserPresenceAdapter
from .round import TurnResult, companion_turn
from .scheduler import BehaviorSource, CompanionScheduler
from .sources_ref import (
    BurstSource,
    DreamSource,
    IntrospectSource,
    PendingTopicsSource,
    RandomSource,
    TimeWindowSource,
)
from .topics import PendingTopicsStore
from .voice import VoiceAdapter

_TAG_STRIP_RE = re.compile(r"\[[a-zA-Z_]+\]")


def _strip_tags(text: str) -> str:
    """Remove ``[emotion]``/``[agent]`` markers before handing text to TTS."""
    return _TAG_STRIP_RE.sub("", text or "").strip()


@dataclass
class CompanionConfig:
    """Everything the caller owns: persona text, paths, dials, and the payloads
    the proactive behaviours speak. No model choice, no adapter lives here."""

    persona: str = ""
    system_prompt: str = ""  # static reply-model system prefix (persona + rules)
    state_path: str | Path = "state.json"
    scheduler_state_path: str | Path = "scheduler_state.json"
    dials: PersonaDials | None = None
    engine_config: Config = DEFAULT_CONFIG
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    # Rolling cap on the private chat history: keep only the most recent N turns
    # (user+assistant messages counted individually). This bounds both memory and
    # what is re-sent to the backend each turn (an unbounded chat overflows context
    # and re-uploads the whole conversation every turn). None = unbounded (legacy).
    history_cap: int | None = 40
    # Behaviour payloads stay caller-supplied — no prompt text baked into feltstate.
    time_window_payloads: list[tuple[int, int, str]] = field(default_factory=list)
    random_payloads: list[str] = field(default_factory=list)
    burst_payloads: list[str] = field(default_factory=list)


class Companion:
    """One handle owning the Engine + a reply backend + the proactive heartbeat.

    Construct with the pluggables (source / backend / frontend / voice /
    presence); :meth:`say` runs a foreground turn; :meth:`start` runs the
    heartbeat.
    """

    def __init__(
        self,
        cfg: CompanionConfig,
        *,
        source: AffectSource,
        backend: LLMBackend,
        frontend: FrontendAdapter,
        voice: VoiceAdapter,
        presence: UserPresenceAdapter,
        topics: PendingTopicsStore | None = None,
        extra_sources: list[BehaviorSource] | None = None,
    ) -> None:
        self.cfg = cfg
        self._lock = threading.RLock()
        # ``threading.RLock`` protects cross-thread access (foreground loop vs.
        # scheduler thread), but it is re-entrant for every coroutine running on
        # the same event-loop thread. Keep one asyncio lock per event loop so two
        # concurrent ``say()`` calls cannot overlap voice/frontend work or mutate
        # shared history concurrently. A weak-key map avoids pinning closed loops
        # created by repeated ``asyncio.run`` calls.
        self._async_lock_guard = threading.Lock()
        self._async_turn_locks: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, weakref.ReferenceType[asyncio.Lock]
        ] = weakref.WeakKeyDictionary()
        self.eng = Engine(
            source=source,
            state_path=cfg.state_path,
            config=cfg.engine_config,
            persona=cfg.persona,
            dials=cfg.dials,
        )
        self.backend = backend
        self.frontend = frontend
        self.voice = voice
        self.presence = presence
        self.topics = topics
        self.history: list[dict] = []
        sources = self._build_sources(topics)
        if extra_sources:
            sources.extend(extra_sources)
        self.scheduler = CompanionScheduler(
            self.eng,
            presence=presence,
            dispatcher=_CompanionDispatcher(self),
            sources=sources,
            state_path=cfg.scheduler_state_path,
            cfg=cfg.scheduler,
            topics=topics,
            lock=self._lock,
        )

    def _build_sources(self, topics: PendingTopicsStore | None) -> list[BehaviorSource]:
        srcs: list[BehaviorSource] = [DreamSource(self.eng), IntrospectSource()]
        if topics is not None:
            srcs.append(PendingTopicsSource(topics))
        if self.cfg.time_window_payloads:
            srcs.append(TimeWindowSource(self.cfg.time_window_payloads))
        if self.cfg.random_payloads:
            srcs.append(RandomSource(self.cfg.random_payloads))
        if self.cfg.burst_payloads:
            srcs.append(BurstSource(self.cfg.burst_payloads))
        return srcs

    def _async_turn_lock(self) -> asyncio.Lock:
        """Return the coroutine-level turn lock for the running event loop.

        ``threading.RLock`` serialises different OS threads, but considers every
        coroutine on one event-loop thread the same owner. Without this second
        lock, ``asyncio.gather(pet.say(...), pet.say(...))`` can overlap TTS,
        frontend updates, and shared-history mutations.
        """
        loop = asyncio.get_running_loop()
        with self._async_lock_guard:
            lock_ref = self._async_turn_locks.get(loop)
            lock = lock_ref() if lock_ref is not None else None
            if lock is None:
                lock = asyncio.Lock()
                # Store a weak value too: once no turn uses the lock, it must not
                # retain the event loop through asyncio's internal loop binding.
                self._async_turn_locks[loop] = weakref.ref(lock)
            return lock

    async def say(self, user_text: str) -> TurnResult:
        """A foreground user turn: estimate → reply → express → speak.

        The per-event-loop async lock serialises sibling coroutines, while
        ``self._lock`` serialises this turn against the scheduler thread. Both
        are held for the complete turn, including frontend and voice adapters.
        """
        async with self._async_turn_lock():
            with self._lock:
                prev = copy.deepcopy(self.eng.state)
                result = companion_turn(
                    self.eng,
                    self.backend,
                    self.history,
                    user_text,
                    system_prompt=self.cfg.system_prompt,
                    history_cap=self.cfg.history_cap,
                )
                await self._express_and_speak(prev, result)
                return result

    async def _proactive_say(self, kind: str, payload: str) -> None:
        """A heartbeat-initiated turn. ``payload`` is the line/prompt to voice;
        empty payloads (silent dream / introspection) speak nothing.

        Holds ``self._lock`` for the full duration so a concurrent foreground
        :meth:`say` cannot interleave engine/history mutations with this call.
        """
        if not payload:
            return
        async with self._async_turn_lock():
            with self._lock:
                prev = copy.deepcopy(self.eng.state)
                # skip_tick: the payload is the agent's own proactive prompt, not a user
                # message — do not estimate affect from it (avoids self-reinforcement).
                # record_role="proactive": the prompt is the agent's own instruction, so
                # store it under a non-user marker. It is kept for the record but never
                # replayed to the backend as a user turn, so next turn the model does not
                # mistake the agent's proactive prompt for something the user said (#51).
                result = companion_turn(
                    self.eng,
                    self.backend,
                    self.history,
                    payload,
                    system_prompt=self.cfg.system_prompt,
                    skip_tick=True,
                    history_cap=self.cfg.history_cap,
                    record_role="proactive",
                )
                await self._express_and_speak(prev, result)

    async def _express_and_speak(self, prev: AffectState, result: TurnResult) -> None:
        label = expression_signal(prev, self.eng.state)
        if label is not None:
            token = self.frontend.label_to_token(label)
            if token is not None:
                await self.frontend.push_expression(token)
        speak_text = _strip_tags(result.reply)
        if self.voice.should_speak(speak_text):
            await self.voice.synthesize(speak_text, result.emotion_label or "")

    def start(self) -> None:
        """Start the proactive heartbeat thread."""
        self.scheduler.start()

    def stop(self, timeout: float = 2.0) -> bool:
        """Stop the heartbeat thread. Return ``True`` once it has exited.

        A ``False`` result means an adapter is still blocking the heartbeat; the
        scheduler keeps its thread handle and refuses a duplicate ``start()``
        until that original thread has actually finished.
        """
        return self.scheduler.stop(timeout=timeout)


_log = logging.getLogger(__name__)


class _CompanionDispatcher(BehaviorDispatcher):
    """Routes a fired behaviour back through the companion's own speak path."""

    def __init__(self, companion: Companion) -> None:
        self.companion = companion

    def dispatch(self, kind: str, payload: str) -> bool:
        try:
            asyncio.run(self.companion._proactive_say(kind, payload))
            return True
        except Exception:
            _log.exception(
                "companion dispatcher: unhandled error in proactive dispatch (kind=%s)", kind
            )
            return False


def run_companion(
    cfg: CompanionConfig,
    *,
    source: AffectSource,
    backend: LLMBackend,
    frontend: FrontendAdapter,
    voice: VoiceAdapter,
    presence: UserPresenceAdapter,
    topics: PendingTopicsStore | None = None,
    extra_sources: list[BehaviorSource] | None = None,
) -> Companion:
    """Build a :class:`Companion` and start its heartbeat. Returns it."""
    pet = Companion(
        cfg,
        source=source,
        backend=backend,
        frontend=frontend,
        voice=voice,
        presence=presence,
        topics=topics,
        extra_sources=extra_sources,
    )
    pet.start()
    return pet
