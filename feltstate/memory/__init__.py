"""feltstate.memory — a decaying 5W1H fact store the agent reads and writes itself.

*Memory is a tool, not a controller.* The agent decides when to recall or record
a fact; the library only handles decay, dedup, and visibility. Nothing here is
auto-injected into a prompt.
"""

from .canon import Canon
from .extract import FactExtractor, LLMFactExtractor, commit_to_canon

__all__ = ["Canon", "FactExtractor", "LLMFactExtractor", "commit_to_canon"]
