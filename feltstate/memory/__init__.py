"""feltstate.memory — a decaying 5W1H fact store the agent reads and writes itself.

*Memory is a tool, not a controller.* The agent decides when to recall or record
a fact; the library only handles decay, dedup, and visibility. Nothing here is
auto-injected into a prompt.
"""

from .canon import Canon

__all__ = ["Canon"]
