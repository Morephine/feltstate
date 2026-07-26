"""The shared defaults in feltstate.config must be immutable in fact, not by label.

Every sub-config is ``@dataclass(frozen=True)``, and ``DEFAULT_CONFIG`` is the
default argument of every :class:`~feltstate.engine.Engine`. Frozen only stops
rebinding the *attribute*; a dict hanging off one is process-global mutable
state that any caller can rewrite for every engine in the process — including
engines already constructed, since they hold the same object.
"""

from __future__ import annotations

import pytest

from feltstate.affect.pressure import compute_power
from feltstate.config import DEFAULT_CONFIG, PressureConfig, TraitConfig
from feltstate.state import Relationship, Traits

# (owner, attribute) for every mapping the configurator exposes as a default.
_DEFAULT_MAPS = [
    ("traits.baseline_pull", DEFAULT_CONFIG.traits.baseline_pull),
    ("pressure.power_weights", DEFAULT_CONFIG.pressure.power_weights),
    ("pressure.release_duration_min", DEFAULT_CONFIG.pressure.release_duration_min),
    ("pressure.aftertaste_duration_min", DEFAULT_CONFIG.pressure.aftertaste_duration_min),
]


@pytest.mark.parametrize(("name", "mapping"), _DEFAULT_MAPS, ids=[n for n, _ in _DEFAULT_MAPS])
def test_shared_default_maps_reject_writes(name, mapping):
    """The headline: DEFAULT_CONFIG.pressure.power_weights["safety"] = 99 used to
    silently reweight the power appraisal of every engine in the process."""
    key = next(iter(mapping))
    with pytest.raises(TypeError):
        mapping[key] = 99
    with pytest.raises(TypeError):
        del mapping[key]


def test_a_fresh_config_is_just_as_read_only():
    """Not only the DEFAULT_CONFIG instance — the class default itself."""
    with pytest.raises(TypeError):
        TraitConfig().baseline_pull["optimism"] = 0.9
    with pytest.raises(TypeError):
        PressureConfig().power_weights["safety"] = 99


def test_a_caller_supplied_dict_still_works():
    """Immutable *defaults* must not mean the knob stops being a knob: a caller
    passing an ordinary dict is still read the same way."""
    cfg = PressureConfig(power_weights={"safety": 1.0})
    rel = Relationship(safety=0.8)
    assert abs(compute_power(Traits(), rel, cfg) - 0.8) < 1e-9
