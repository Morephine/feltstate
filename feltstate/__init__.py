"""feltstate — persistent affect and provenance-aware memory for LLM agents.

Four boundaries, one reference architecture:

* **Independently appraised, not self-reported.** Affect is *estimated* each turn by a
  component separate from the reply model (an :class:`AffectSource`), so the
  reply model cannot directly author the stored state.
* **Memory has a lifecycle.** :class:`Canon` stores structured facts that can
  decay, strengthen, be corrected, and retain history; optional lifecycle tools
  add provenance fingerprints, lineage, ageing, deletion plans, and audit logs.
* **Tool, not controller.** feltstate produces *state*; it never injects
  commands ("be sad now") into the prompt. The reply model receives state as
  context and uses it when generating a response.
* **First-person context.** Affect is rendered into a compact first-person block
  for the reply model — a prompt/interface design choice, not a claim about
  consciousness or subjective experience.

The core is standard-library only and includes configurable affect dynamics,
cache-safe injection, structured memory, and optional companion orchestration.

Quickstart::

    from feltstate import Engine, KeywordSource

    eng = Engine(source=KeywordSource(), state_path="state.json")
    eng.tick([{"role": "user", "content": "I finally shipped it!! thank you"}])
    print(eng.render())   # -> a first-person felt block to prepend to the agent's context
"""

from .config import (
    DEFAULT_CONFIG,
    Config,
    PersonaDials,
    TirednessConfig,
    agent_scale_config,
)
from .dream import DEFAULT_PHRASEBOOK, Dream, Fragment, Phrasebook
from .engine import Engine
from .memory.canon import Canon
from .memory.context import get_turn_context, load_turns
from .memory.skill import (
    RatingGate,
    SkillRatifier,
    add_skill,
    ratify_skill,
    rating_priority,
    recall_skills,
    record_rating,
    record_task_rating,
    review_skills,
)
from .render import build_injection, render_agent_feeling, render_felt_block
from .sleep import Tiredness
from .sources.base import AffectSource
from .sources.keyword import KeywordSource
from .sources.llm import LLMSource
from .state import (
    AffectDelta,
    AffectState,
    Mood,
    PressureBars,
    PressureState,
    Relationship,
    Traits,
)
from .timeawareness import time_since_phrase

__version__ = "0.2.0a2"

__all__ = [
    "Engine",
    "AffectState",
    "AffectDelta",
    "Mood",
    "Traits",
    "Relationship",
    "PressureState",
    "PressureBars",
    "Config",
    "DEFAULT_CONFIG",
    "PersonaDials",
    "agent_scale_config",
    "AffectSource",
    "KeywordSource",
    "LLMSource",
    "Canon",
    "get_turn_context",
    "load_turns",
    "add_skill",
    "record_rating",
    "record_task_rating",
    "recall_skills",
    "review_skills",
    "ratify_skill",
    "rating_priority",
    "RatingGate",
    "SkillRatifier",
    "Fragment",
    "Phrasebook",
    "Dream",
    "DEFAULT_PHRASEBOOK",
    "Tiredness",
    "TirednessConfig",
    "time_since_phrase",
    "render_felt_block",
    "render_agent_feeling",
    "build_injection",
]
