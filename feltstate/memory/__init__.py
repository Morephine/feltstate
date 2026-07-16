"""feltstate.memory — a decaying 5W1H fact store with explicit recall and write tools.

*Memory is a tool, not a controller.* Callers decide when to recall or record a
fact; the library handles decay, deduplication, and visibility. Nothing here is
automatically injected into a prompt.
"""

from .canon import Canon
from .context import get_turn_context, get_turn_range_context, load_turns
from .extract import FactExtractor, LLMFactExtractor, commit_to_canon
from .skill import (
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

__all__ = [
    "Canon",
    "get_turn_context",
    "get_turn_range_context",
    "load_turns",
    "FactExtractor",
    "LLMFactExtractor",
    "commit_to_canon",
    "add_skill",
    "record_rating",
    "record_task_rating",
    "recall_skills",
    "review_skills",
    "ratify_skill",
    "rating_priority",
    "RatingGate",
    "SkillRatifier",
]
