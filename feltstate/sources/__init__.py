"""Affect sources — the pluggable "how does it feel?" measurement layer."""

from .base import AffectSource, latest_user_text
from .keyword import KeywordSource
from .llm import LLMSource

# VheartSource ships in `.vheart` but needs torch/transformers/peft, which
# are optional. Import it explicitly: `from feltstate.sources.vheart import
# VheartSource`. Keeping it out of this __init__ lets the core stay
# zero-deps.

__all__ = ["AffectSource", "KeywordSource", "LLMSource", "latest_user_text"]
