"""Affect sources — the pluggable "how does it feel?" measurement layer."""
from .base import AffectSource, latest_user_text
from .keyword import KeywordSource
from .llm import LLMSource

__all__ = ["AffectSource", "KeywordSource", "LLMSource", "latest_user_text"]
