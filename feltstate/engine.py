"""feltstate.engine — the top-level facade that ties every layer together.

:class:`Engine` is the one object an application talks to. It owns the persistent
:class:`~feltstate.state.AffectState`, drives one full update per conversation
turn, and renders the result as a first-person context block for the reply
model. Everything underneath — the affect dynamics, the optional
permanent imprints, the time sense, the renderers — is wired together here so the
caller never has to.

The loop, in one sentence: a pluggable :class:`~feltstate.sources.base.AffectSource`
*estimates* a character reaction for this turn (appraised from the conversation,
not self-reported by the reply model); the dynamics integrate that reading into slow
traits, a fast mood, and a multi-bar pressure cooker (all of which decay back
toward neutral when the conversation goes quiet); the result is rendered into
discrete first-person phrasing and fed back **inside the latest user message**
so the prompt cache stays warm.

Three design rules carried through from the rest of the package:

* **Estimated, not self-reported.** Affect comes from ``source.read(...)``, a
  component separate from whatever model writes the agent's replies. The engine
  does not let the reply model directly author the stored state; the reading
  remains an external estimate, not ground truth.
* **Tool, not controller.** The engine produces *state* and renders it; it never
  injects an instruction ("be sad now"). :meth:`render` and :meth:`inject` hand
  the reply model a descriptive state block without prescribing behaviour.
* **First-person form.** :meth:`render` emits a first-person block (via
  :func:`~feltstate.render.felt.render_felt_block`), not a data dump.

Quickstart::

    from feltstate import Engine, KeywordSource

    eng = Engine(source=KeywordSource(), state_path="state.json")
    eng.tick([{"role": "user", "content": "I finally shipped it!! thank you"}])
    prompt = eng.inject("what should we build next?")  # felt block + user words
    # ... send `prompt` as the user turn; persona/rules stay static up top ...
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from .affect import (
    apply_trait_shift,
    baseline_from_imprints,
    check_echo,
    compute_tide,
    decay_imprints,
    echo_mood_nudge,
    ingest_milestones,
    smooth_labels,
    update_mood,
    update_relationship,
    update_traits,
)
from .affect import (
    step as pressure_step,
)
from .affect.imprint import Imprint
from .config import DEFAULT_CONFIG, Config, PersonaDials
from .memory.canon import Canon
from .render import build_injection, render_felt_block
from .sleep import Tiredness
from .sources.base import AffectSource, latest_user_text
from .state import AffectState
from .timeawareness import now_phrase, time_since_phrase

_log = logging.getLogger(__name__)

__all__ = ["Engine"]

# One "reference tick" of elapsed wall-clock time. Every per-tick decay rate in
# the config (traits baseline pull, pressure idle_decay, relationship
# tension_decay) is expressed *per reference tick*; the engine converts real
# elapsed seconds into reference ticks so those decays become a function of
# wall-clock time rather than of how often tick() is called. One minute is the
# natural anchor — companion ticks are conversation turns minutes apart.
_REFERENCE_TICK_SECONDS = 60.0

# A dream's residue is forgotten once its tracked magnitude has decayed below
# this — an explicit, decaying value, independent of the total mood (finding #16).
_DREAM_FORGET_EPS = 0.04


# A reading this unsure is not trusted as a usable signal — it is treated as
# an idle tick (state decays on its own clocks, nothing is integrated from it).
# Chief case: an estimation source that errored and returned a neutral,
# low-confidence delta. Tune per source; sources that never emit low confidence
# are unaffected.
_CONFIDENCE_FLOOR = 0.2


def _trusted_reading(delta):
    """Gate a turn's reading by its ``confidence``.

    ``confidence`` was a published field that nothing downstream consumed, so an
    unsure or failed reading changed the agent exactly as much as a certain one.
    Here a reading below :data:`_CONFIDENCE_FLOOR` has its *continuous* signal
    neutralised: valence zeroed and discrete emotion labels dropped. The
    integrators then treat the turn as idle for that channel — traits skip their
    EWMA (no label), pressure cools, mood eases toward neutral — i.e. the state
    decays on its own clocks instead of absorbing a signal it cannot trust. A
    confident reading passes through untouched.

    ``milestones`` are deliberately *kept*: a deep appraised event carries its
    own severity and does not depend on the continuous reading being confident
    (and a failed estimate returns no milestones anyway). This is a
    trust *gate*, not a graded weight; graded confidence-scaling of each
    integrator's learning rate is a larger, separable change.
    """
    if delta.confidence >= _CONFIDENCE_FLOOR:
        return delta
    from dataclasses import replace

    return replace(delta, valence=0.0, labels=[])


class Engine:
    """Top-level facade: integrate affect per turn, render it back, persist it.

    Parameters
    ----------
    source
        The :class:`~feltstate.sources.base.AffectSource` that *estimates* each
        turn's reading. This is the appraisal seam — supply
        :class:`~feltstate.sources.keyword.KeywordSource` for a zero-dependency
        baseline, :class:`~feltstate.sources.llm.LLMSource` for a model-backed
        reading, or your own subclass.
    state_path
        Where the :class:`~feltstate.state.AffectState` JSON lives. Loaded on
        construction if present, created fresh otherwise. A sibling
        ``<name>.meta.json`` holds the engine's own bookkeeping (the last *real*
        user-turn timestamp and the optional imprint list), kept separate so the
        state schema stays a pure dataclass round-trip.
    config
        The :class:`~feltstate.config.Config` bundle of every tunable. Defaults
        to :data:`~feltstate.config.DEFAULT_CONFIG`.
    persona
        Optional short, free-text description of who the character is. Passed
        straight through to ``source.read`` (plain sources ignore it; model-backed
        ones fold it into their estimation prompt). Kept out of code on purpose
        — it is the caller's to supply, and it never becomes an instruction.
    dials
        Optional :class:`~feltstate.config.PersonaDials` describing how this
        character *expresses* feeling. They tilt release-channel preference in the
        pressure cooker and the closing tone line of the rendered block; they
        never change *what* is felt. ``None`` uses neutral dials.
    canon
        Optional :class:`~feltstate.memory.canon.Canon` fact store. The engine
        does not write to it automatically (memory is the agent's tool to use);
        it is held here only so an application has one handle for everything.
    """

    def __init__(
        self,
        source: AffectSource,
        *,
        state_path: str | Path = "state.json",
        config: Config = DEFAULT_CONFIG,
        persona: str = "",
        dials: PersonaDials | None = None,
        canon: Canon | None = None,
        max_imprints: int = 128,
    ) -> None:
        self.source = source
        self.config = config
        self.persona = persona or ""
        self.dials = dials if dials is not None else PersonaDials()
        self.canon = canon
        self.max_imprints = int(max_imprints)

        self.state_path = Path(state_path)
        # Sidecar for engine bookkeeping the AffectState schema does not carry:
        # the last *real* user-turn timestamp (drives the time-sense line) and
        # the optional permanent-imprint list. Kept beside the state file.
        self._meta_path = self.state_path.with_name(
            self.state_path.stem + ".meta" + (self.state_path.suffix or ".json")
        )

        # Load (or create) the persistent felt state.
        self.state: AffectState = AffectState.load(self.state_path)

        # Load engine bookkeeping (best-effort; never fatal).
        self._last_user_ts: str | None = None
        self.imprints: list[Imprint] = []
        # Label-hysteresis bookkeeping (see affect.smooth): the labels currently
        # shown, plus a candidate top label and how long it has been trying to win.
        self._labels_committed: list[str] = []
        self._label_candidate: str | None = None
        self._label_streak: int = 0
        self._last_dream: str = ""  # most recent dream's text, for possible recall
        # Explicit, decaying magnitude of the last dream's mood residue. Tracked
        # (not inferred from the total mood) so an unrelated mood can neither keep
        # a spent dream alive nor instantly cancel a fresh one (finding #16). It
        # decays over elapsed time on the same fast-mood clock; when it falls below
        # _DREAM_FORGET_EPS the dream text is forgotten. ``_dream_residue_ts``
        # anchors that decay to when the dream applied (dreams can happen off the
        # tick path), so it is a function of real elapsed time like every other
        # decay — not of how many ticks happen to follow.
        self._dream_residue: float = 0.0
        self._dream_residue_ts: str | None = None
        self.tiredness: Tiredness = Tiredness()  # sleep-pressure accumulator (when to dream)
        self._load_meta()

    # ------------------------------------------------------------------ #
    # The per-turn update                                                #
    # ------------------------------------------------------------------ #
    def tick(self, messages: list[dict], *, now: datetime | None = None) -> AffectState:
        """Advance the felt state by one conversation turn and return it.

        ``messages`` is the recent conversation, oldest first, as
        ``[{"role": "user"|"assistant", "content": str}, ...]``. The steps, in
        order:

        1. **Estimate** this turn's reading with ``source.read`` (grounded in the
           current state and persona).
        2. **Integrate** it: asymmetric-EWMA traits, then the trait-pulled felt
           mood.
        3. **Pressure** — one full cooker tick (accumulate / cool / maybe release
           / advance phase), power-aware and personality-tilted.
        4. **Imprints** (optional) — any deep ``delta.milestones`` (warmth /
           trauma family) become permanent imprints whose one-time trait shift is
           applied once; existing imprints age and may echo on the latest user
           text.
        5. **Persist** — record the reading in the rolling history, stamp
           ``last_tick_ts``, and atomically save the state plus the engine
           sidecar.

        The same wall clock drives every time-based effect this turn, so the
        dynamics stay self-consistent. Pass ``now`` to drive an explicit clock
        (a simulated or monotonic one); it defaults to
        ``datetime.now(timezone.utc)`` (UTC-aware). If you supply a naive
        ``now``, it is treated as UTC — a lossy assumption documented here so
        it is not silent.

        **Elapsed-time decay (frequency-invariance).** Every decay this turn —
        the trait baseline pull, the pressure bar cooldown, the relationship
        tension decay — is advanced by the *real elapsed time* since the previous
        tick (converted to reference ticks of one minute), not by a flat one unit
        per call. Ticking a quiet conversation every minute therefore decays the
        state the same amount as ticking it every five minutes over the same span,
        instead of five times as fast. Each tick is floored at a minimum of one
        reference tick, so a burst of sub-minute ticks (and every caller that does
        not care about wall-clock precision) still behaves exactly as one unit of
        decay per call — the historical contract — while genuinely spaced-out ticks
        decay by their real elapsed time.

        Calling this with an empty / neutral ``messages`` is the intended way to
        let the state *decay back toward neutral* between real turns: the source
        returns a low-confidence neutral delta, the trait/mood integrators do
        only their baseline pull, and the pressure bars cool — tick it on a timer
        and a quiet conversation eases home.
        """
        now = now or datetime.now(timezone.utc)
        ts = now.isoformat()
        # Real elapsed time since the previous tick, in reference ticks (one
        # minute each), floored at 1.0. The floor keeps sub-minute / rapid ticks
        # at the historical "one unit of decay per call" while letting genuinely
        # spaced ticks decay by their real elapsed span — the frequency-invariance
        # seam threaded into every decay below.
        elapsed_ticks = self._elapsed_ticks(self.state.last_tick_ts, now)

        # (1) Appraise this turn's reading via the source.
        delta = self.source.read(messages, baseline=self.state, persona=self.persona)
        # A reading the source is too unsure of (chiefly: an estimate that
        # errored and fell back to neutral) is not trusted as a usable signal —
        # it is neutralised to a signal-less delta so this turn integrates as an
        # idle tick (state decays on its own clocks) instead of absorbing an
        # untrusted signal. Every integrator below sees this trust-gated reading;
        # only the rolling history keeps the raw estimate for audit.
        eff = _trusted_reading(delta)

        # (2) Integrate into slow traits, then the trait-pulled fast mood. The
        #     trait baseline pull decays over the real elapsed span (finding #11);
        #     the mood EWMA is a per-event smoothing of this turn's reading and is
        #     left per-tick.
        traits = update_traits(
            self.state.traits, eff, self.config.traits, elapsed_ticks=elapsed_ticks
        )
        mood = update_mood(self.state.mood, eff, traits, self.config.mood)
        # Top-label hysteresis so a noisy source can't flip the shown label every
        # turn (keeps the rendered block cache-stable).
        mood.labels, self._label_candidate, self._label_streak = smooth_labels(
            mood.labels,
            self._labels_committed,
            self._label_candidate,
            self._label_streak,
            self.config.mood.label_smooth_ticks,
        )
        self._labels_committed = list(mood.labels)

        # (3) Optional permanent imprints. Deep appraised events (the warmth /
        #     trauma families) leave a lasting mark; their one-time trait shift is
        #     applied *before* the pressure tick so power/floors see the updated
        #     temperament this turn. Uses the raw delta: a milestone is a discrete
        #     appraised event with its own severity, not gated by continuous
        #     confidence (a failed reading returns no milestones anyway).
        traits, fired_echoes = self._apply_imprints(delta, traits, messages, ts)
        # An imprint the user just re-touched ("the deadline again", "that kind
        # thing you said") gives the fast mood a small, *bounded* colouring in the
        # imprint's own direction — an old hurt stings afresh, an old kindness warms
        # afresh — proportional to how vivid the mark still is. It rides on top of
        # the integrated mood, shows up in the rendered block / mood numbers, and
        # then decays through the ordinary tick dynamics like any other feeling.
        # Bounded by construction (a fraction of the gap to a sub-unit target), so
        # repeated echoes cannot drive the mood to the extreme. State, not command.
        mood = echo_mood_nudge(fired_echoes, mood)

        # (4) Evolve the bond with the user from this turn (its tension/safety
        #     feed the pressure tick), then run one full pressure-cooker tick.
        relationship = update_relationship(self.state.relationship, eff, self.config.relationship)
        # update_relationship applies exactly one reference tick of tension decay
        # internally; make that decay elapsed-time-based too (finding #11) by
        # draining the remaining (elapsed_ticks - 1) ticks here. Tension decay is
        # subtractive with a floor at 0, which composes exactly, so one tick inside
        # plus (k-1) here equals k ticks of decay — and at k == 1 nothing extra is
        # drained (identical to the pre-change behaviour). Done in the engine
        # because the relationship dynamics module owns only the single-tick step.
        extra_ticks = max(0.0, elapsed_ticks - 1.0)
        if extra_ticks > 0.0 and relationship.unresolved_tension > 0.0:
            relationship.unresolved_tension = max(
                0.0,
                relationship.unresolved_tension
                - self.config.relationship.tension_decay * extra_ticks,
            )
        pressure = pressure_step(
            self.state.pressure,
            delta=eff,
            traits=traits,
            relationship=relationship,
            dials=self.dials,
            cfg=self.config.pressure,
            ts=ts,
            elapsed_ticks=elapsed_ticks,
        )

        # Commit the integrated layers back onto the state.
        self.state.traits = traits
        self.state.mood = mood
        self.state.relationship = relationship
        self.state.pressure = pressure

        # (5) Rolling history of readings + bookkeeping, then persist.
        self.state.history.append(
            {
                "ts": ts,
                "valence": round(float(delta.valence), 4),
                "arousal": round(float(delta.arousal), 4),
                "labels": list(delta.labels or []),
            }
        )
        self.state.history = self.state.history[-50:]
        # Read the mood's rising/falling tide from the (now-updated) history.
        self.state.mood.tide = compute_tide(self.state.history, self.config.mood)
        self.state.last_tick_ts = ts

        # A turn that actually carried a user message re-anchors the time sense:
        # the "last time we really spoke" clock used by render(). A bare decay
        # tick (no user text) does not reset it, so the felt distance keeps
        # growing while the conversation is quiet.
        if latest_user_text(messages).strip():
            self._last_user_ts = ts

        # (6) Sleep pressure: accrue tiredness for this tick's activity (it drives
        #     *when* the agent next dreams), and forget the last dream once its
        #     tracked residue has decayed away (text lifespan = mood lifespan).
        self.tiredness.rise(self.state.mood.arousal, now, self.config.tiredness)
        # Decay the explicit dream-residue magnitude over the elapsed span, on the
        # same fast-mood clock the nudge itself rides (finding #16), then forget
        # the dream text once it is spent. Tracking the residue explicitly means a
        # later unrelated mood can neither prop up a spent dream nor cancel a fresh
        # one — only real elapsed time forgets it. Anchored to its *own* timestamp
        # (set when the dream applied), not to last_tick_ts, so a dream that
        # happened off the tick path still decays by real elapsed time.
        self._decay_dream_residue(now)

        self.save()
        return self.state

    def _apply_imprints(
        self,
        delta,
        traits,
        messages: list[dict],
        ts: str,
    ):
        """Fold the optional permanent-imprint layer into ``traits`` for this tick.

        New deep milestones become imprints (deduped by stable id); each fresh
        imprint's one-time trait shift is applied exactly once. Existing imprints
        age by elapsed time and may flare ("echo") when the user raises their
        subject again. Returns ``(traits, fired)`` — the (possibly) shifted traits
        and the imprints that echoed this turn (empty when none did); the caller
        turns ``fired`` into a bounded colouring of the felt mood. The imprint list
        is updated in place on ``self``.
        """
        # Age the existing imprints to *now* first (cheap; tiny daily decay).
        fired: list[Imprint] = []
        if self.imprints:
            decay_imprints(self.imprints, ts)
            # An echo re-vivifies intensity and (via the returned fired list, wired
            # up in tick()) gives the touched memory a small bounded push on the
            # felt mood — it does *not* re-shift the permanent traits.
            fired = check_echo(self.imprints, latest_user_text(messages), ts)

        # Ingest any new deep events from this turn's milestones.
        new_imprints = ingest_milestones(getattr(delta, "milestones", None) or [], ts)
        if new_imprints:
            known_ids = {imp.id for imp in self.imprints}
            for imp in new_imprints:
                if imp.id in known_ids:
                    continue  # dedup: the same event ingested twice does not stack
                # Apply the one-time permanent trait shift (idempotent on `imp`).
                traits = apply_trait_shift(traits, imp)
                self.imprints.append(imp)
                known_ids.add(imp.id)

        # Bound the imprint list defensively: a source that reports the same deep
        # event every turn must not grow memory without limit. When over the cap,
        # keep the most vivid marks (current intensity, then original depth).
        if len(self.imprints) > self.max_imprints:
            self.imprints.sort(key=lambda i: (i.intensity, i.severity), reverse=True)
            del self.imprints[self.max_imprints :]

        # Recompute the persistent trait resting point from the imprints we are
        # *keeping* (after any trim above). This is what makes an imprint's lift
        # durable — update_traits relaxes each trait toward this shifted point, so
        # the mark survives arbitrarily many idle ticks instead of decaying back to
        # neutral. Deriving it from the kept list (rather than mutating it as each
        # imprint arrives) means a trimmed imprint's offset is dropped here too, so
        # trimming can never leave an orphaned permanent effect.
        traits.baseline = baseline_from_imprints(self.imprints)

        return traits, fired

    # ------------------------------------------------------------------ #
    # Rendering affective state for the reply model                         #
    # ------------------------------------------------------------------ #
    def render(self, *, header: str = "[how I feel right now]") -> str:
        """Render the current state as a first-person felt block.

        Builds the time-awareness line from the engine's "last real user turn"
        timestamp and the present moment, then defers to
        :func:`~feltstate.render.felt.render_felt_block`. The time line is only
        included when there *is* something worth saying: a fuzzy "how long it's
        been" phrase is emitted only once the gap exceeds the configured gate
        (short gaps are the model's own short-term sense), while the precise
        "now" anchor is always available — so within an active conversation the
        line reads as just the current moment, and after a long silence it leads
        with the felt distance back.

        The block uses coarse discrete phrase bands, so adjacent ticks whose
        numbers drift only slightly render byte-identically — which is what keeps
        :meth:`inject` cheap to cache.
        """
        now = datetime.now(timezone.utc)
        since = time_since_phrase(self._last_user_ts, now, self.config.time)
        present = now_phrase(now)

        if since:
            time_line = f"{since} since we last spoke · now {present}"
        else:
            time_line = f"now {present}"

        return render_felt_block(
            self.state,
            dials=self.dials,
            time_line=time_line,
            cfg=self.config,
            header=header,
        )

    def inject(self, user_message: str) -> str:
        """Return the current user turn with the felt block riding on its front.

        Thin wrapper over :meth:`render` +
        :func:`~feltstate.render.inject.build_injection`. The result is meant to
        be sent as the **content of the current user turn**, after the static,
        cached system/persona prefix — never spliced into the system prompt
        (which would change every turn and bust the cache). See
        :mod:`feltstate.render.inject` for the full discipline.
        """
        return build_injection(self.render(), user_message)

    # ------------------------------------------------------------------ #
    # Dreaming (optional, off the per-turn path)                         #
    # ------------------------------------------------------------------ #
    def dream(self, *, fragments: list | None = None, phrasebook=None, rng=None, now=None):
        """Produce one dream and apply its faint residue to the felt mood.

        Call this on a *sleep* cycle — between sessions, or on a long idle —
        **not** every turn. It gathers dream material (rich
        :class:`~feltstate.dream.Fragment` objects you pass in, or a thin
        best-effort set from the current state when ``fragments`` is ``None``),
        recombines it illogically (see :mod:`feltstate.dream`), nudges the mood by
        the dream's small residue (which then decays through the ordinary tick
        dynamics), stashes the dream text for possible later recall, and returns
        the :class:`~feltstate.dream.Dream`. ``now`` stamps the residue's decay
        anchor (defaults to ``datetime.now(timezone.utc)``), so the tracked residue ages by
        real elapsed time from when the dream happened.

        The reply model is never told to feel anything — the residue shifts the
        estimated mood with no explicit prompt attribution; the cause is not
        surfaced to the model.
        """
        from .dream import DEFAULT_PHRASEBOOK, gather_fragments
        from .dream import dream as _dream

        material = list(fragments) if fragments is not None else gather_fragments(self.state)
        d = _dream(
            material,
            phrasebook=phrasebook or DEFAULT_PHRASEBOOK,
            cfg=self.config.dream,
            rng=rng,
        )
        # Apply the residue as a small, causally-opaque nudge to the felt mood; it
        # then carries and decays through the ordinary tick dynamics.
        m = self.state.mood
        m.valence = max(-1.0, min(1.0, m.valence + d.valence))
        m.arousal = max(0.0, min(1.0, m.arousal + d.arousal))
        self._last_dream = d.text
        # Seed the explicit, decaying residue magnitude with the size of the nudge
        # this dream just applied (finding #16), and anchor its decay clock to when
        # the dream happened. Subsequent ticks decay it over elapsed time; the dream
        # text is remembered exactly as long as the residue is, regardless of what
        # other moods come and go. A null dream (no text / no nudge) seeds nothing
        # and is not remembered.
        self._dream_residue = abs(float(d.valence)) + abs(float(d.arousal))
        self._dream_residue_ts = (now or datetime.now(timezone.utc)).isoformat()
        self.save()
        return d

    def maybe_dream(
        self,
        *,
        idle_minutes: float,
        now: datetime | None = None,
        fragments: list | None = None,
        phrasebook=None,
        rng=None,
    ):
        """Dream *iff* sleep pressure says it is time — otherwise return ``None``.

        This is the *when*; :meth:`dream` is the *how*. It brings the tiredness
        accumulator up to ``now`` (catching up any idle time since its last
        update), and if it is :meth:`~feltstate.sleep.Tiredness.ready` — tired
        enough, alone at least ``idle_gate_minutes``, and past the refractory floor
        since the last dream — runs one dream, discharges the pressure to zero, and
        returns the :class:`~feltstate.dream.Dream`. Otherwise it persists the
        risen pressure and returns ``None``, changing nothing else.

        Call it on a sleep-cycle check (a periodic idle tick), not every turn. When
        the agent is *not yet* tired enough, it simply isn't ready to sleep — in a
        fuller system that idle moment is where introspection would run instead.
        """
        now = now or datetime.now(timezone.utc)
        self.tiredness.rise(self.state.mood.arousal, now, self.config.tiredness)
        if not self.tiredness.ready(now, idle_minutes, self.config.tiredness):
            self.save()  # persist the risen pressure even when we don't dream
            return None
        d = self.dream(fragments=fragments, phrasebook=phrasebook, rng=rng, now=now)
        self.tiredness.discharge(now)
        self.save()
        return d

    @staticmethod
    def _elapsed_ticks(prev_ts: str | None, now: datetime) -> float:
        """Real elapsed time since ``prev_ts`` in reference ticks, floored at 1.0.

        A reference tick is :data:`_REFERENCE_TICK_SECONDS` (one minute). This is
        the frequency-invariance seam: every decay in :meth:`tick` scales by this,
        so the same real elapsed time decays the state the same amount however
        often :meth:`tick` is called. The 1.0 floor keeps rapid / sub-minute ticks
        (and any caller ignoring wall-clock precision) at the historical one-unit-
        per-call behaviour; genuinely spaced ticks decay by their real span. A
        missing or unparseable / non-monotonic previous timestamp yields the floor.

        **Legacy naive timestamps.** State files written before the UTC migration
        (``datetime.now()`` without ``timezone.utc``) may carry naive ISO strings.
        When ``prev_ts`` has no tzinfo suffix it is assumed UTC here — a lossy
        assumption if the saving process was in a non-UTC timezone, but the
        alternative (a TypeError at subtraction time) is worse. New writes from
        :meth:`tick` always produce UTC-aware ISO strings, so the issue heals
        after the first tick on any migrated state file.
        """
        if not prev_ts:
            return 1.0
        try:
            prev = datetime.fromisoformat(prev_ts.replace("Z", "+00:00"))
            if prev.tzinfo is None:
                prev = prev.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return 1.0
        # Normalize `now` to the same awareness so the subtraction never raises.
        _now = now
        if _now.tzinfo is None:
            _now = _now.replace(tzinfo=timezone.utc)
        elapsed_s = (_now - prev).total_seconds()
        return max(1.0, elapsed_s / _REFERENCE_TICK_SECONDS)

    def _decay_dream_residue(self, now: datetime) -> None:
        """Age the tracked dream residue by real elapsed time; forget when spent.

        The residue rides the same fast-mood clock the nudge itself decays on:
        ``residue *= (1 - va_alpha) ** elapsed_ticks`` over the span since the
        residue's own anchor (advanced to ``now`` each call). Because it is an
        explicit tracked value — not a guess read off the total mood — an unrelated
        later mood can neither keep a spent dream alive nor cancel a fresh one;
        only elapsed time forgets it. Below :data:`_DREAM_FORGET_EPS` the residue
        is zeroed and the dream text dropped. No-op once there is nothing to age.
        """
        if self._dream_residue <= 0.0:
            return
        ticks = self._elapsed_ticks(self._dream_residue_ts, now)
        self._dream_residue *= (1.0 - self.config.mood.va_alpha) ** ticks
        self._dream_residue_ts = now.isoformat()
        if self._dream_residue < _DREAM_FORGET_EPS:
            self._dream_residue = 0.0
            self._dream_residue_ts = None
            self._last_dream = ""

    # ------------------------------------------------------------------ #
    # Skill region (optional; needs a Canon). Thin pass-throughs.        #
    # ------------------------------------------------------------------ #
    def recall_skills(self, query: str, **kw) -> list[dict]:
        """The agent's skill-lookup tool. Returns ``[]`` when no canon is attached.
        Skills are retrieve-on-demand only — never auto-injected, never in
        :meth:`render`/:meth:`inject`, so the static prompt prefix is untouched."""
        if self.canon is None:
            return []
        from .memory.skill import recall_skills

        return recall_skills(self.canon, query, **kw)

    def record_rating(
        self,
        skill_id_or_trigger: str,
        rating: int,
        *,
        source: str = "human",
        note: str = "",
        actor: str = "self",
    ) -> dict:
        """Fold one human 1/2/3 rating into a skill (the real-use verdict; may
        auto-promote/retire). ``{}`` when no canon is attached, or ``source`` is not
        observed (the reply model cannot rate its own work)."""
        if self.canon is None:
            return {}
        from .memory.skill import record_rating

        return record_rating(
            self.canon, skill_id_or_trigger, rating, source=source, note=note, actor=actor
        )

    def record_task_rating(self, skill_ids, rating: int, *, source: str = "human") -> list[dict]:
        """Apply one completed-task rating across the skills that task used. ``[]``
        if no canon is attached."""
        if self.canon is None:
            return []
        from .memory.skill import record_task_rating

        return record_task_rating(self.canon, skill_ids, rating, source=source)

    def review_skills(self, **kw) -> list[dict]:
        """Read-only overview of the skill library, for introspection — where the
        agent tidies its own skills (merge, retire, ratify). ``[]`` if no canon.
        Consolidation is this reflective work, not an automatic daemon pass."""
        if self.canon is None:
            return []
        from .memory.skill import review_skills

        return review_skills(self.canon, **kw)

    # ------------------------------------------------------------------ #
    # Persistence                                                        #
    # ------------------------------------------------------------------ #
    def save(self) -> None:
        """Persist the state and the engine sidecar.

        Each file is written atomically (write-to-tmp then rename), but the two
        writes are **not** transactionally consistent as a pair — a crash between
        them leaves the state file updated and the sidecar stale (or vice versa).
        Acceptable for the current private-prototype phase; a caller that needs
        cross-file consistency should wrap both writes in its own atomic boundary.
        """
        self.state.save(self.state_path)
        self._save_meta()

    def _load_meta(self) -> None:
        """Best-effort load of the engine sidecar (last-user ts + imprints).

        Invalid JSON *or schema shape* is quarantined instead of crashing boot or
        being silently overwritten on the next save. Values are parsed into
        locals first so a partially corrupt sidecar cannot half-mutate the engine.
        """
        if not self._meta_path.is_file():
            return
        try:
            import json

            data = json.loads(self._meta_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("engine meta root must be a JSON object")

            last_user_ts = data.get("last_user_ts") or None
            raw_imprints = data.get("imprints") or []
            if not isinstance(raw_imprints, list):
                raise ValueError("engine meta imprints must be a JSON array")
            imprints = [Imprint.from_dict(d) for d in raw_imprints if isinstance(d, dict)]

            raw_labels = data.get("labels_committed") or []
            if not isinstance(raw_labels, list):
                raise ValueError("engine meta labels_committed must be a JSON array")
            labels_committed = [str(label) for label in raw_labels]
            label_candidate = data.get("label_candidate") or None
            label_streak = int(data.get("label_streak") or 0)
            last_dream = str(data.get("last_dream", "") or "")
            dream_residue = float(data.get("dream_residue", 0.0) or 0.0)
            dream_residue_ts = data.get("dream_residue_ts") or None
            tiredness = Tiredness.from_dict(data.get("tiredness"))
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            quarantined = self._quarantine_meta()
            where = (
                f"quarantined to {quarantined.name}"
                if quarantined is not None
                else "could not be quarantined (left in place)"
            )
            _log.warning(
                "feltstate: engine meta file %s corrupt/unreadable (%s); %s; "
                "continuing with default bookkeeping",
                self._meta_path,
                exc,
                where,
            )
            return

        self._last_user_ts = last_user_ts
        self.imprints = imprints
        self._labels_committed = labels_committed
        self._label_candidate = label_candidate
        self._label_streak = label_streak
        self._last_dream = last_dream
        self._dream_residue = dream_residue
        self._dream_residue_ts = dream_residue_ts
        self.tiredness = tiredness

    def _quarantine_meta(self) -> Path | None:
        """Move a corrupt engine sidecar aside, preserving its original bytes."""
        try:
            import time

            stamp = int(time.time())
            dest = self._meta_path.with_name(f"{self._meta_path.name}.corrupt-{stamp}")
            n = 1
            while dest.exists():
                dest = self._meta_path.with_name(f"{self._meta_path.name}.corrupt-{stamp}.{n}")
                n += 1
            self._meta_path.replace(dest)
            return dest
        except OSError:
            return None

    def _save_meta(self) -> None:
        """Atomically write the engine sidecar beside the state file."""
        import json

        payload = {
            "last_user_ts": self._last_user_ts,
            "imprints": [imp.to_dict() for imp in self.imprints],
            "labels_committed": list(self._labels_committed),
            "label_candidate": self._label_candidate,
            "label_streak": int(self._label_streak),
            "last_dream": self._last_dream,
            "dream_residue": round(float(self._dream_residue), 6),
            "dream_residue_ts": self._dream_residue_ts,
            "tiredness": self.tiredness.to_dict(),
        }
        p = self._meta_path
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
