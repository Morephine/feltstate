"""Engine — the façade: one tick integrates a reading, persists, renders, injects,
decays when quiet, and folds in optional imprints."""

from datetime import datetime, timezone

from feltstate.engine import Engine
from feltstate.sources.keyword import KeywordSource
from feltstate.state import AffectState

_elapsed_ticks = Engine._elapsed_ticks


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


class _ImprintEchoSource(KeywordSource):
    """Turn 1 lays down one negative imprint with an echo cue; every later turn
    is a flat, confident, neutral reading and emits no new milestone — so any
    change in the felt mood on a later turn can only be the echo firing."""

    def __init__(self):
        super().__init__()
        self._turn = 0

    def read(self, messages, *, baseline, persona=""):
        d = super().read(messages, baseline=baseline, persona=persona)
        self._turn += 1
        # Neutralise the continuous reading so the echo is the only mood mover.
        d.valence, d.arousal, d.labels, d.confidence = 0.0, 0.4, [], 0.9
        d.milestones = (
            [
                {
                    "kind": "trauma_betrayal",
                    "severity": 1.0,
                    "echo_keywords": ["the deadline"],
                    "label": "the deadline",
                }
            ]
            if self._turn == 1
            else []
        )
        return d


def test_fired_echo_colours_the_felt_mood_and_render(tmp_path):
    # Findings #6/#7: a re-triggered imprint must reach the felt state. Two engines
    # take an identical first turn (laying the imprint); on turn two only one user
    # message re-touches the imprint's cue. That echo must move the mood and the
    # rendered block — otherwise the echo is computed and dropped.
    fired = Engine(source=_ImprintEchoSource(), state_path=tmp_path / "fired.json")
    fired.tick([{"role": "user", "content": "hello"}])
    assert len(fired.imprints) == 1 and fired.imprints[0].valence_sign == -1
    fired.tick([{"role": "user", "content": "what happened with the deadline again?"}])
    assert fired.imprints[0].echo_count == 1  # the echo actually fired

    quiet = Engine(source=_ImprintEchoSource(), state_path=tmp_path / "quiet.json")
    quiet.tick([{"role": "user", "content": "hello"}])
    quiet.tick([{"role": "user", "content": "what happened with the weather again?"}])
    assert quiet.imprints[0].echo_count == 0  # cue not touched -> no echo

    # A negative imprint stinging afresh pushes the felt valence more negative...
    assert fired.state.mood.valence < quiet.state.mood.valence
    # ...and the difference is large enough to change the rendered first-person block.
    assert fired.render() != quiet.render()


def test_repeated_echoes_keep_the_mood_bounded(tmp_path):
    # The echo nudge must not let a user drive the mood to the rail by hammering
    # the same cue turn after turn: throttle + decay + the asymptotic nudge hold it.
    eng = Engine(source=_ImprintEchoSource(), state_path=tmp_path / "s.json")
    eng.tick([{"role": "user", "content": "hello"}])  # lay the imprint
    worst = 0.0
    for _ in range(200):
        eng.tick([{"role": "user", "content": "the deadline, the deadline"}])
        worst = min(worst, eng.state.mood.valence)
    assert eng.imprints[0].echo_count >= 1  # echoes did fire over the run
    assert -1.0 < worst  # never pinned to the negative rail
    assert worst > -0.9  # stays a colouring, does not diverge


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


class _OneWarmthThenQuietSource(KeywordSource):
    """Turn 1 lays a single deep warmth imprint; every later turn is a flat,
    confident, neutral, milestone-less reading — i.e. an idle decay tick. Lets a
    test lay one imprint and then watch the trait behave over a long quiet run."""

    def __init__(self):
        super().__init__()
        self._turn = 0

    def read(self, messages, *, baseline, persona=""):
        d = super().read(messages, baseline=baseline, persona=persona)
        self._turn += 1
        d.valence, d.arousal, d.labels, d.confidence = 0.0, 0.4, [], 0.9
        d.milestones = [{"kind": "warmth_care", "severity": 1.0}] if self._turn == 1 else []
        return d


def test_imprint_trait_lift_survives_a_long_quiet_stretch(tmp_path):
    # Findings #4/#5, at the engine level: one warmth imprint lifts optimism, and
    # after a very long quiet run (only idle decay ticks) the lift is STILL clearly
    # present — it does not evaporate back to the 0.5 baseline the way it used to.
    eng = Engine(source=_OneWarmthThenQuietSource(), state_path=tmp_path / "s.json")
    before = eng.state.traits.optimism
    eng.tick([{"role": "user", "content": "you really matter to me"}])
    lifted = eng.state.traits.optimism
    assert lifted > before  # the warmth imprint lifted optimism
    assert eng.state.traits.baseline.get("optimism", 0.5) > 0.5  # resting point moved

    for _ in range(300):  # long quiet stretch: idle decay only, no new signal
        eng.tick([{"role": "user", "content": "the wooden table is brown"}])

    # Still lifted — within a whisker of the shifted resting point, not decayed to
    # neutral (old behaviour would have optimism back near 0.50 well before here).
    assert eng.state.traits.optimism > 0.54
    assert eng.state.traits.baseline.get("optimism", 0.5) > 0.5  # offset persisted


def test_imprint_baseline_persists_across_engine_reload(tmp_path):
    # The durable resting point must survive a save/load cycle (it lives in the
    # state JSON), so a restarted companion keeps the mark, not just the process.
    path = tmp_path / "s.json"
    first = Engine(source=_OneWarmthThenQuietSource(), state_path=path)
    first.tick([{"role": "user", "content": "you really matter to me"}])
    saved = first.state.traits.baseline.get("optimism")
    assert saved and saved > 0.5

    second = Engine(source=KeywordSource(), state_path=path)
    assert second.state.traits.baseline.get("optimism") == saved


def test_trimmed_imprint_leaves_no_orphaned_baseline_offset(tmp_path):
    # Finding #10: trimming happens AFTER the one-time shift is applied. If the
    # persistent baseline were accumulated per-imprint it would keep a trimmed
    # imprint's offset forever. Because the engine RE-DERIVES the baseline from the
    # kept imprints each tick, a trimmed imprint contributes nothing to the resting
    # point — the offset reflects exactly the imprints still held.
    counter = {"n": 0}

    class DistinctWarmthSource(KeywordSource):
        """A fresh, distinct deep warmth event every turn (unique label -> unique
        id), so nothing dedups and the cap must actually trim old imprints."""

        def read(self, messages, *, baseline, persona=""):
            d = super().read(messages, baseline=baseline, persona=persona)
            counter["n"] += 1
            d.valence, d.arousal, d.labels, d.confidence = 0.0, 0.4, [], 0.9
            d.milestones = [
                {"kind": "warmth_care", "severity": 1.0, "label": f"event-{counter['n']}"}
            ]
            return d

    from feltstate.affect.imprint import baseline_from_imprints

    cap = 5
    eng = Engine(source=DistinctWarmthSource(), state_path=tmp_path / "s.json", max_imprints=cap)
    for _ in range(30):  # far more distinct events than the cap
        eng.tick([{"role": "user", "content": "you matter to me"}])

    assert len(eng.imprints) == cap  # trimming occurred
    # The stored resting point equals what the KEPT imprints imply — no orphaned
    # contribution from any of the trimmed ones lingering in the baseline.
    assert eng.state.traits.baseline == baseline_from_imprints(eng.imprints)


# ------------------------------------------------------------------ #
# Timezone-awareness safety                                           #
# ------------------------------------------------------------------ #


def test_elapsed_ticks_naive_ts_with_aware_now_does_not_raise():
    """A naive ISO timestamp persisted from an older tick must not crash when
    subtracted from a UTC-aware ``now`` (the previously-observed TypeError)."""
    naive_ts = "2025-01-01T12:00:00"  # naive — no tzinfo suffix
    aware_now = datetime(2025, 1, 1, 12, 2, 0, tzinfo=timezone.utc)  # 2 minutes later
    # Must not raise TypeError; must return >= 1.0 (2 min / 1 min = 2.0 reference ticks).
    result = _elapsed_ticks(naive_ts, aware_now)
    assert result == 2.0


def test_elapsed_ticks_aware_ts_with_aware_now_does_not_raise():
    """Both sides UTC-aware must also work correctly."""
    aware_ts = "2025-01-01T12:00:00+00:00"
    aware_now = datetime(2025, 1, 1, 12, 3, 0, tzinfo=timezone.utc)  # 3 minutes later
    result = _elapsed_ticks(aware_ts, aware_now)
    assert result == 3.0


def test_tick_with_aware_now_does_not_raise(tmp_path):
    """Engine.tick() called with a UTC-aware ``now`` on a freshly-created engine
    (which stores its first naive ts then gets a second aware ``now``) must not
    raise TypeError at any point in the lifecycle."""
    eng = Engine(source=KeywordSource(), state_path=tmp_path / "s.json")
    msgs = [{"role": "user", "content": "hello"}]
    # First tick with naive now (establishes a naive persisted ts).
    naive_now = datetime(2025, 6, 1, 10, 0, 0)
    eng.tick(msgs, now=naive_now)
    # Second tick with aware now — previously triggered the TypeError.
    aware_now = datetime(2025, 6, 1, 10, 5, 0, tzinfo=timezone.utc)
    state = eng.tick(msgs, now=aware_now)  # must not raise
    assert state is not None


def test_non_object_meta_root_is_quarantined(tmp_path):
    state_path = tmp_path / "s.json"
    meta_path = tmp_path / "s.meta.json"
    meta_path.write_text("[1]", encoding="utf-8")

    eng = Engine(source=KeywordSource(), state_path=state_path)

    assert eng.imprints == []
    assert eng._last_user_ts is None
    assert not meta_path.exists()
    quarantined = list(tmp_path.glob("s.meta.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "[1]"
