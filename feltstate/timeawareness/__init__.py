"""feltstate.timeawareness — long-term time sense for the felt-state layer.

Short-term time is the model's own; this package only supplies what it lacks:
a *fuzzy* sense of how long it's been since you last spoke, and a *precise*
anchor for the present moment. See :mod:`feltstate.timeawareness.relative_time`.
"""
from .relative_time import time_since_phrase, now_phrase

__all__ = ["time_since_phrase", "now_phrase"]
