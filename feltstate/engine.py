"""feltstate.engine — the top-level facade that ties every layer together.

:class:`Engine` is the one object an application talks to. It owns the persistent
:class:`~feltstate.state.AffectState`, drives one full update per conversation
turn, and renders the result back as a first-person felt block the agent reads as
*its own* feeling. Everything underneath — the affect dynamics, the optional
permanent imprints, the time sense, the renderers — is wired together here so the
caller never has to.

The loop, in one sentence: a pluggable :class:`~feltstate.sources.base.AffectSource`
*measures* how the agent feels this turn (ground truth, not self-report); the
dynamics integrate that reading into slow traits, a fast mood, and a multi-bar
pressure cooker (all of which decay back toward neutral when the conversation
goes quiet); the result is rendered into discrete first-person phrasing and fed
back **inside the latest user message** so the prompt cache stays warm.

Three design rules carried through from the rest of the package:

* **Ground truth, not self-report.** Affect comes from ``source.read(...)``, a
  component separate from whatever model writes the agent's replies. The engine
  never lets the reply model decide how it feels.
* **Tool, not controller.** The engine produces *state* and renders it; it never
  injects an instruction ("be sad now"). :meth:`render` and :meth:`inject` hand
  the agent its feeling and trust it to act as itself.
* **Identity-merge.** :meth:`render` emits a first-person block (via
  :func:`~feltstate.render.felt.render_felt_block`), not a data dump.

Quickstart::

    from feltstate import Engine, KeywordSource

    eng = Engine(source=KeywordSource(), state_path="state.json")
    eng.tick([{"role": "user", "content": "I finally shipped it!! thank you"}])
    prompt = eng.inject("what should we build next?")  # felt block + user words
    # ... send `prompt` as the user turn; persona/rules stay static up top ...
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .affect import (
    apply_trait_shift,
    check_echo,
    compute_tide,
    decay_imprints,
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

__all__ = ["Engine"]

# A dream is forgotten once its mood-nudge has decayed this close to baseline.
_DREAM_FORGET_EPS = 0.04


class Engine:
    """Top-level facade: integrate affect per turn, render it back, persist it.

    Parameters
    ----------
    source
        The :class:`~feltstate.sources.base.AffectSource` that *measures* each
        turn's reading. This is the ground-truth seam — supply
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
        ones fold it into their measurement prompt). Kept out of code on purpose
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
        self.tiredness: Tiredness = Tiredness()  # sleep-pressure accumulator (when to dream)
        self._load_meta()

    # ------------------------------------------------------------------ #
    # The per-turn update                                                #
    # ------------------------------------------------------------------ #
    def tick(self, messages: list[dict]) -> AffectState:
        """Advance the felt state by one conversation turn and return it.

        ``messages`` is the recent conversation, oldest first, as
        ``[{"role": "user"|"assistant", "content": str}, ...]``. The steps, in
        order:

        1. **Measure** this turn's reading with ``source.read`` (grounded in the
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

        The same wall clock (``datetime.now()``, naive local) drives every
        time-based effect this turn, so the dynamics stay self-consistent.

        Calling this with an empty / neutral ``messages`` is the intended way to
        let the state *decay back toward neutral* between real turns: the source
        returns a low-confidence neutral delta, the trait/mood integrators do
        only their baseline pull, and the pressure bars cool — tick it on a timer
        and a quiet conversation eases home.
        """
        now = datetime.now()
        ts = now.isoformat()

        # (1) Measure the ground-truth reading for this turn.
        delta = self.source.read(messages, baseline=self.state, persona=self.persona)

        # (2) Integrate into slow traits, then the trait-pulled fast mood.
        traits = update_traits(self.state.traits, delta, self.config.traits)
        mood = update_mood(self.state.mood, delta, traits, self.config.mood)
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
        #     temperament this turn. Done on the imprint-adjusted `traits`.
        traits = self._apply_imprints(delta, traits, messages, ts)

        # (4) Evolve the bond with the user from this turn (its tension/safety
        #     feed the pressure tick), then run one full pressure-cooker tick.
        relationship = update_relationship(self.state.relationship, delta, self.config.relationship)
        pressure = pressure_step(
            self.state.pressure,
            delta=delta,
            traits=traits,
            relationship=relationship,
            dials=self.dials,
            cfg=self.config.pressure,
            ts=ts,
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
        #     residue has decayed back to baseline (text lifespan = mood lifespan).
        self.tiredness.rise(self.state.mood.arousal, now, self.config.tiredness)
        if self._last_dream and self._dream_residue_spent():
            self._last_dream = ""

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
        subject again. Returns the (possibly) shifted traits; the imprint list is
        updated in place on ``self``.
        """
        # Age the existing imprints to *now* first (cheap; tiny daily decay).
        if self.imprints:
            decay_imprints(self.imprints, ts)
            # An echo only re-vivifies intensity; it does not re-shift traits.
            check_echo(self.imprints, latest_user_text(messages), ts)

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

        return traits

    # ------------------------------------------------------------------ #
    # Rendering the felt state back to the agent                         #
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
        now = datetime.now()
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
    def dream(self, *, fragments: list | None = None, phrasebook=None, rng=None):
        """Produce one dream and apply its faint residue to the felt mood.

        Call this on a *sleep* cycle — between sessions, or on a long idle —
        **not** every turn. It gathers dream material (rich
        :class:`~feltstate.dream.Fragment` objects you pass in, or a thin
        best-effort set from the current state when ``fragments`` is ``None``),
        recombines it illogically (see :mod:`feltstate.dream`), nudges the mood by
        the dream's small residue (which then decays through the ordinary tick
        dynamics), stashes the dream text for possible later recall, and returns
        the :class:`~feltstate.dream.Dream`.

        The reply model is never told to feel anything — the agent simply wakes
        slightly moved, with no cause it can trace.
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
        # Apply the residue as a small, untraceable nudge to the felt mood; it
        # then carries and decays through the ordinary tick dynamics.
        m = self.state.mood
        m.valence = max(-1.0, min(1.0, m.valence + d.valence))
        m.arousal = max(0.0, min(1.0, m.arousal + d.arousal))
        self._last_dream = d.text
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
        now = now or datetime.now()
        self.tiredness.rise(self.state.mood.arousal, now, self.config.tiredness)
        if not self.tiredness.ready(now, idle_minutes, self.config.tiredness):
            self.save()  # persist the risen pressure even when we don't dream
            return None
        d = self.dream(fragments=fragments, phrasebook=phrasebook, rng=rng)
        self.tiredness.discharge(now)
        self.save()
        return d

    def _dream_residue_spent(self) -> bool:
        """True once a dream's faint mood-nudge has decayed back to baseline — at
        which point the dream is forgotten (its stored text is dropped)."""
        m = self.state.mood
        return abs(m.valence) < _DREAM_FORGET_EPS and abs(m.arousal - 0.4) < _DREAM_FORGET_EPS

    # ------------------------------------------------------------------ #
    # Persistence                                                        #
    # ------------------------------------------------------------------ #
    def save(self) -> None:
        """Persist the state and the engine sidecar (both atomic writes)."""
        self.state.save(self.state_path)
        self._save_meta()

    def _load_meta(self) -> None:
        """Best-effort load of the engine sidecar (last-user ts + imprints)."""
        if not self._meta_path.is_file():
            return
        try:
            import json

            data = json.loads(self._meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self._last_user_ts = data.get("last_user_ts") or None
        self.imprints = [
            Imprint.from_dict(d) for d in (data.get("imprints") or []) if isinstance(d, dict)
        ]
        self._labels_committed = list(data.get("labels_committed") or [])
        self._label_candidate = data.get("label_candidate") or None
        self._label_streak = int(data.get("label_streak") or 0)
        self._last_dream = str(data.get("last_dream", "") or "")
        self.tiredness = Tiredness.from_dict(data.get("tiredness"))

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
            "tiredness": self.tiredness.to_dict(),
        }
        p = self._meta_path
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
