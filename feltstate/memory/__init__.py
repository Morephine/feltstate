"""feltstate.memory — a decaying 5W1H fact store the agent reads and writes itself.

*Memory is a tool, not a controller.* The agent decides when to recall or record
a fact; the library only handles decay, dedup, and visibility. Nothing here is
auto-injected into a prompt.
"""

from .canon import Canon
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
