"""Engine — the façade: one tick integrates a reading, persists, renders, injects,
decays when quiet, and folds in optional imprints."""

from feltstate.engine import Engine
from feltstate.sources.keyword import KeywordSource
from feltstate.state import AffectState


def test_tick_persists_and_records_history(tmp_path):
    eng = Engine(source=KeywordSource(), state_path=tmp_path / "s.json")
    state = eng.tick([{"role": "user", "content": "thank you, this is wonderful"}])
    assert isinstance(state, AffectState)
    assert (tmp_path / "s.json").exists()
    assert len(state.history) == 1
    assert state.last_tick_ts is not None
    assert state.mood.valence >= 0.0  # a positive reading didn't push it negative


def test_render_is_first_person_block_and_inject_carries_user_words(tmp_path):
    eng = Engine(source=KeywordSource(), state_path=tmp_path / "s.json")
    eng.tick([{"role": "user", "content": "hello there"}])
    block = eng.render()
    assert block.startswith("[how I feel right now]")
    out = eng.inject("what should we do next?")
    assert "what should we do next?" in out
    assert block.splitlines()[0] in out  # the felt block rides along


def test_state_persists_across_instances(tmp_path):
    path = tmp_path / "s.json"
    first = Engine(source=KeywordSource(), state_path=path)
    first.tick([{"role": "user", "content": "i'm so happy"}])
    # A fresh Engine on the same path loads the prior felt state.
    second = Engine(source=KeywordSource(), state_path=path)
    assert len(second.state.history) >= 1


def test_goes_quiet_and_decays_toward_neutral(tmp_path):
    eng = Engine(source=KeywordSource(), state_path=tmp_path / "s.json")
    for _ in range(10):
        eng.tick([{"role": "user", "content": "yay amazing wonderful so happy"}])
    joy_peak = eng.state.pressure.bars.joy
    assert joy_peak > 0.0  # accumulated some joy
    for _ in range(30):
        eng.tick([{"role": "user", "content": "the wooden table is brown"}])  # no cue -> neutral
    assert eng.state.pressure.bars.joy < joy_peak  # cooled once the good stretch ended


def test_milestones_drive_a_permanent_imprint_with_within_tick_dedup(tmp_path):
    class WarmthSource(KeywordSource):
        """Reports the same deep warmth milestone twice in one tick."""

        def read(self, messages, *, baseline, persona=""):
            delta = super().read(messages, baseline=baseline, persona=persona)
            # Two identical milestones in one tick share an id -> dedup to one.
            delta.milestones = [
                {"kind": "warmth_care", "severity": 1.0},
                {"kind": "warmth_care", "severity": 1.0},
            ]
            return delta

    eng = Engine(source=WarmthSource(), state_path=tmp_path / "s.json")
    before = eng.state.traits.optimism
    eng.tick([{"role": "user", "content": "you really matter to me"}])
    assert len(eng.imprints) == 1  # within-tick duplicates collapsed
    assert eng.imprints[0].valence_sign == +1
    assert eng.state.traits.optimism > before  # the one-time warmth shift was applied


def test_imprint_list_stays_bounded(tmp_path):
    # A pathological source emitting a fresh deep event every turn must not grow
    # memory without bound; the engine caps the imprint list.
    counter = {"n": 0}

    class NoisySource(KeywordSource):
        def read(self, messages, *, baseline, persona=""):
            delta = super().read(messages, baseline=baseline, persona=persona)
            counter["n"] += 1
            delta.milestones = [
                {"kind": "warmth_care", "severity": 0.6, "label": f"event-{counter['n']}"}
            ]
            return delta

    eng = Engine(source=NoisySource(), state_path=tmp_path / "s.json", max_imprints=10)
    for _ in range(40):
        eng.tick([{"role": "user", "content": "thanks"}])
    assert len(eng.imprints) <= 10
