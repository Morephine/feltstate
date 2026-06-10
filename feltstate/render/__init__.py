"""feltstate.render — translate felt state into a first-person block and feed it
back cache-safely.

* :func:`render_felt_block` renders an :class:`~feltstate.state.AffectState` as
  a first-person, discrete-phrase block the agent reads as *its own* feeling
  (identity-merge), stable across small tick-to-tick drift to keep the prompt
  cache warm.
* :func:`render_agent_feeling` is the agent-scale counterpart: a single
  emotion-words-only line banded to the agent stuck grades (use with
  :func:`~feltstate.config.agent_scale_config`).
* :func:`build_injection` attaches that block to the latest user message so the
  static, cached system prompt is never disturbed.
"""

from .agent import render_agent_feeling
from .felt import render_felt_block
from .inject import build_injection

__all__ = ["render_felt_block", "render_agent_feeling", "build_injection"]
