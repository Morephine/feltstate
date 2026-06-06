"""feltstate — give an LLM agent a felt inner life it experiences as its own.

Three ideas, one loop:

* **Ground truth, not self-report.** Affect is *measured* each turn by a
  component separate from the reply model (an :class:`AffectSource`), so the
  agent can't just claim to feel something — it has a state it can't fake.
* **Tool, not controller.** feltstate produces *state*; it never injects
  commands ("be sad now") into the prompt. The agent reads its own felt state
  and decides for itself how to act.
* **Identity-merge.** The state is rendered into a first-person block the agent
  reads as *its own* feelings, never as external data to recite.

Plus the two things the public ecosystem was missing: emotion that **decays
back to neutral** over time (most memory layers decay facts, not feelings), and
a **cache-safe** way to feed it back (so a persistent companion stays cheap).

Quickstart::

    from feltstate import Engine, KeywordSource

    eng = Engine(source=KeywordSource(), state_path="state.json")
    eng.tick([{"role": "user", "content": "I finally shipped it!! thank you"}])
    print(eng.render())   # -> a first-person felt block to prepend to the agent's context
"""

from .config import DEFAULT_CONFIG, Config, PersonaDials, TirednessConfig
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
from .render import build_injection, render_felt_block
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

__version__ = "0.1.0"

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
    "build_injection",
]
