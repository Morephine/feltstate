"""feltstate.memory.canon — a decaying 5W1H fact store ("canon").

*Memory is a tool, not a controller.* The agent decides when to read and write
its own memories; this library only takes care of **decay**, **dedup**, and
**visibility**. It never injects anything into a prompt on its own. Most memory
layers in the ecosystem decay *facts* (vector recency, TTLs); the distinguishing
idea here is that a fact's *felt salience* decays the way a memory fades — slowly,
unless it is reinforced, recalled, or important enough to become permanent.

Each entry is a small 5W1H record::

    {who: {actor}, what: {action, object}, why, when, where,
     intensity, confidence, ts, id, recalls, _reinforce_count, ...}

The salience an entry is shown at is recomputed on every read::

    current = base_intensity
            - age_days * decay_per_day            # fades with time
            + reinforce_count * reinforce_boost   # repeating a fact bumps it
            + min(recall_cap, recalls * recall_each)   # used memory sticks

with two short-circuits driven by :class:`~feltstate.config.MemoryConfig`:

* ``base_intensity >= permanent_above`` → never decays (a permanent memory).
* the result is bucketed into **visible** / **archived** / **forgotten** by
  ``visible_threshold`` and ``archive_threshold``.

Entries dedup by ``(actor | object)``: writing the same fact again *reinforces*
the existing entry instead of duplicating it. Facts can live in a **grey zone**
(:meth:`ask`) until :meth:`confirm`-ed, be :meth:`correct`-ed (the old version is
superseded, not deleted), or :meth:`retract`-ed (marked, not deleted) so the
history is auditable.

Everything here returns plain ``dict`` / ``list[dict]`` — rendering for a human
or for the agent's context is the caller's job (see :mod:`feltstate.render`). The
store will not print and will not phone home.

Storage is line-delimited JSON (one record per line). Given a base ``path`` of
``canon.jsonl``, two sibling files hold the other tiers:

* ``canon.jsonl``          — confirmed facts (the main store)
* ``canon.pending.jsonl``  — the grey zone (undecided / unconfirmed)
* ``canon.archived.jsonl`` — facts compacted out of the main store

All writes are atomic (write-temp-then-replace), so a crash mid-write cannot
corrupt the store.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from ..config import MemoryConfig
from .feeling import blend, derive, neutral_profile, observe

__all__ = ["Canon"]


# --------------------------------------------------------------------------- #
# Small timestamp / id helpers (stdlib only)                                  #
# --------------------------------------------------------------------------- #
def _now_iso() -> str:
    """Current local time as an ISO-8601 string with offset."""
    return datetime.now(timezone.utc).astimezone().isoformat()


def _parse_ts(ts_iso: str) -> datetime | None:
    """Parse an ISO timestamp to an aware ``datetime``; ``None`` if unparseable."""
    try:
        ts = datetime.fromisoformat(str(ts_iso).replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _entry_id(entry: dict) -> str:
    """Stable id derived from ``(actor | object)``.

    Two records describing the same actor + object collapse to the same id, which
    is what drives dedup and lets ``confirm`` / ``correct`` / ``retract`` reference
    a fact by a short handle.
    """
    who = entry.get("who") or {}
    what = entry.get("what") or {}
    actor = (who.get("actor") if isinstance(who, dict) else who) or ""
    obj = (what.get("object") if isinstance(what, dict) else what) or ""
    key = f"{actor}|{obj}".strip().lower()
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]


def _entry_text(entry: dict) -> str:
    """Whole record flattened to lowercase text for keyword matching."""
    return json.dumps(entry, ensure_ascii=False).lower()


def _affect_field(prof: tuple[float, float, float], w: float) -> dict:
    """Pack a sentiment profile + evidence weight into a fact's ``affect`` field."""
    return {
        "pos": round(prof[0], 4),
        "neg": round(prof[1], 4),
        "neu": round(prof[2], 4),
        "w": round(w, 4),
    }


def _lexical_score(query: str, text: str) -> float:
    """Default recall scorer: fraction of the query's tokens present in ``text``.

    Splits on whitespace for space-delimited text and on characters otherwise, so
    it degrades gracefully for CJK. Replace with an embedding-based scorer (passed
    to :meth:`Canon.recall`) for semantic recall.
    """
    q = query.split() if " " in query else list(query)
    q = [t for t in q if t.strip()]
    if not q:
        return 0.0
    return sum(1.0 for t in q if t in text) / len(q)


def _load_jsonl(path: Path) -> list[dict]:
    """Read a line-delimited JSON file into a list, skipping blank/garbage lines."""
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _append_jsonl(path: Path, entry: dict) -> None:
    """Append one record as a JSON line, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _rewrite_jsonl(path: Path, entries: list[dict]) -> None:
    """Atomically replace a file with ``entries`` (write temp, then ``replace``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(e, ensure_ascii=False) for e in entries)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body + "\n" if entries else "", encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------- #
# Canon                                                                       #
# --------------------------------------------------------------------------- #
class Canon:
    """A decaying 5W1H fact store backed by line-delimited JSON files.

    Parameters
    ----------
    path
        Path to the confirmed-facts file (e.g. ``"canon.jsonl"``). The grey-zone
        and archived tiers are sibling files derived from it.
    cfg
        :class:`~feltstate.config.MemoryConfig` supplying every decay/visibility
        constant. Defaults to library defaults.
    """

    def __init__(self, path: str | Path, cfg: MemoryConfig | None = None):
        self.cfg = cfg if cfg is not None else MemoryConfig()
        self.path = Path(path)
        stem = self.path.stem  # "canon" from "canon.jsonl"
        suffix = self.path.suffix or ".jsonl"
        self.pending_path = self.path.with_name(f"{stem}.pending{suffix}")
        self.archived_path = self.path.with_name(f"{stem}.archived{suffix}")

    # ------------------------------------------------------------------ #
    # Decay / salience                                                   #
    # ------------------------------------------------------------------ #
    def _affect_signals(self, entry: dict) -> dict:
        """Derived ``{valence, charge, entropy}`` for a fact's affect, or zeros if
        the fact carries none (M1)."""
        aff = entry.get("affect")
        if not isinstance(aff, dict):
            return {"valence": 0.0, "charge": 0.0, "entropy": 0.0}
        prof = (
            float(aff.get("pos", 0.0)),
            float(aff.get("neg", 0.0)),
            float(aff.get("neu", 1.0)),
        )
        return derive(prof)

    def _current_intensity(self, entry: dict, now: datetime) -> float:
        """Salience an entry should be shown at *now*, after decay and boosts.

        Permanent entries (``base >= permanent_above``) hold their base intensity.
        The decay curve is ``cfg.decay_curve``: ``"linear"`` is the additive
        original (base − age·rate, + reinforce, + recall); ``"fsrs"`` (M2) is a
        stretched-exponential whose rate slows with a fact's importance and whose
        tail is fattened for negative-valence facts — low memories linger while
        bright ones fade. Repeats and recalls slow the fade either way. With
        ``salience_charge_weight`` (M1) set, emotionally flat facts are dimmed in
        what's *shown*, so frequency without feeling stops crowding out meaning —
        the reinforce of charged facts is untouched. Floors at 0.
        """
        cfg = self.cfg
        base = float(entry.get("intensity", cfg.default_intensity))
        if base >= cfg.permanent_above:
            return base
        ts = _parse_ts(entry.get("ts", ""))
        age_days = max(0.0, (now - ts).total_seconds() / 86400.0) if ts else 0.0
        reinforce = int(entry.get("_reinforce_count", 0))
        recalls = int(entry.get("recalls", 0))
        sig = self._affect_signals(entry)

        if cfg.decay_curve == "fsrs":
            # Importance (emotional charge + repetition + recall) slows the rate;
            # valence sign picks the tail — negative lingers, positive fades fast.
            importance = min(
                1.0,
                sig["charge"] * 0.6
                + (reinforce / (1.0 + reinforce)) * 0.3
                + min(cfg.recall_boost_cap, recalls * cfg.recall_boost_each),
            )
            lam = cfg.decay_lambda * math.exp(-cfg.decay_importance_mu * importance)
            beta = cfg.decay_beta_durable if sig["valence"] < 0 else cfg.decay_beta_fast
            current = base * math.exp(-lam * (age_days**beta))
        else:
            recall_boost = min(cfg.recall_boost_cap, recalls * cfg.recall_boost_each)
            current = (
                base - age_days * cfg.decay_per_day + reinforce * cfg.reinforce_boost + recall_boost
            )

        scw = cfg.salience_charge_weight
        if scw > 0.0:
            current *= 1.0 - scw * (1.0 - sig["charge"])
        return max(0.0, current)

    def _tier(self, current: float) -> str:
        """Bucket a salience into ``"visible"`` / ``"archived"`` / ``"forgotten"``."""
        if current >= self.cfg.visible_threshold:
            return "visible"
        if current >= self.cfg.archive_threshold:
            return "archived"
        return "forgotten"

    def _is_active(self, entry: dict) -> bool:
        """A fact is active unless it was retracted or superseded by a correction."""
        return not (entry.get("_retracted") or entry.get("_superseded_by"))

    def _render(self, entry: dict, now: datetime) -> dict:
        """Project a stored record into a clean, decay-annotated view dict.

        Internal bookkeeping keys (``_reinforce_count`` etc.) are dropped; the
        decayed salience and derived tier are added so the caller can render or
        sort without re-deriving them.
        """
        current = self._current_intensity(entry, now)
        sig = self._affect_signals(entry)
        ts = _parse_ts(entry.get("ts", ""))
        who = entry.get("who") or {}
        what = entry.get("what") or {}
        out = {
            "id": _entry_id(entry),
            "actor": (who.get("actor") if isinstance(who, dict) else who) or "",
            "action": (what.get("action") if isinstance(what, dict) else "") or "",
            "object": (what.get("object") if isinstance(what, dict) else what) or "",
            "why": entry.get("why", "") or "",
            "when": entry.get("when", "") or "",
            "where": entry.get("where", "") or "",
            "ts": entry.get("ts", ""),
            "intensity": round(current, 3),
            "base_intensity": round(float(entry.get("intensity", self.cfg.default_intensity)), 3),
            "confidence": round(float(entry.get("confidence", 0.9)), 3),
            "permanent": float(entry.get("intensity", self.cfg.default_intensity))
            >= self.cfg.permanent_above,
            "tier": self._tier(current),
            "reinforced": int(entry.get("_reinforce_count", 0)),
            "recalls": int(entry.get("recalls", 0)),
            "valence": sig["valence"],  # M1: how the fact leans (-1..1)
            "charge": sig["charge"],  # M1: how emotionally loaded (0..1)
            "entropy": sig["entropy"],  # M1: ambivalence / uncertainty
            "age_days": round((now - ts).total_seconds() / 86400.0, 1) if ts else None,
        }
        return out

    # ------------------------------------------------------------------ #
    # Internal write helpers (dedup / reinforce)                         #
    # ------------------------------------------------------------------ #
    def _build_entry(
        self,
        actor: str,
        obj: str,
        *,
        action: str = "",
        why: str = "",
        when: str = "",
        where: str = "",
        intensity: float | None = None,
        confidence: float = 0.9,
        default_intensity: float | None = None,
        emotion: float | None = None,
    ) -> dict:
        """Assemble a fresh 5W1H record from keyword fields.

        ``emotion`` (a valence in [-1, 1], optional) seeds the fact's
        evidence-weighted affect (M1): a young feeling starting from neutral, moved
        by this first reading in proportion to ``confidence``.
        """
        if default_intensity is None:
            default_intensity = self.cfg.default_intensity
        entry = {
            "ts": _now_iso(),
            "who": {"actor": actor},
            "what": {"action": action or "", "object": obj},
            "why": why or "",
            "when": when or "",
            "where": where or "",
            "intensity": float(intensity if intensity is not None else default_intensity),
            "confidence": float(confidence),
            "recalls": 0,
        }
        if emotion is not None:
            prof, w = blend(
                neutral_profile(), self.cfg.sentiment_prior_weight, observe(emotion), confidence
            )
            entry["affect"] = _affect_field(prof, w)
        return entry

    def _write_or_reinforce(
        self, path: Path, entry: dict, *, emotion: float | None = None, confidence: float = 0.9
    ) -> dict:
        """Append ``entry``, or reinforce an existing active entry with the same id.

        Reinforcing bumps ``_reinforce_count`` (which slows that fact's decay) and
        refreshes its timestamp — salience reinforce is unchanged. If ``emotion``
        is given, this turn's reading is *also* folded into the fact's
        evidence-weighted affect (M1): a recurring feeling settles and gains
        inertia; a recurring *flat* mention keeps the fact neutral. Retracted /
        superseded entries are ignored when matching, so retract-then-readd yields
        a fresh active fact.
        """
        new_id = _entry_id(entry)
        existing = _load_jsonl(path)
        for e in existing:
            if not self._is_active(e):
                continue
            if _entry_id(e) == new_id:
                e["_reinforce_count"] = int(e.get("_reinforce_count", 0)) + 1
                e["_last_reinforced"] = _now_iso()
                e["ts"] = _now_iso()
                if emotion is not None:
                    aff = e.get("affect")
                    if isinstance(aff, dict):
                        prof = (
                            float(aff.get("pos", 0.0)),
                            float(aff.get("neg", 0.0)),
                            float(aff.get("neu", 1.0)),
                        )
                        w = float(aff.get("w", self.cfg.sentiment_prior_weight))
                    else:
                        prof, w = neutral_profile(), self.cfg.sentiment_prior_weight
                    prof, w = blend(prof, w, observe(emotion), confidence)
                    e["affect"] = _affect_field(prof, w)
                _rewrite_jsonl(path, existing)
                return self._render(e, datetime.now(timezone.utc))
        _append_jsonl(path, entry)
        return self._render(entry, datetime.now(timezone.utc))

    def _find_active(self, target: str, path: Path) -> tuple[list[dict], int]:
        """Locate an active entry by exact id, then by keyword. ``-1`` if none.

        Returns the full entry list (so the caller can mutate and rewrite) and the
        index of the match. Exact id wins over a keyword substring hit.
        """
        target_l = str(target).lower()
        entries = _load_jsonl(path)
        for i, e in enumerate(entries):
            if self._is_active(e) and _entry_id(e) == target_l:
                return entries, i
        for i, e in enumerate(entries):
            if self._is_active(e) and target_l in _entry_text(e):
                return entries, i
        return entries, -1

    # ------------------------------------------------------------------ #
    # Public API — write                                                 #
    # ------------------------------------------------------------------ #
    def add(
        self,
        actor: str,
        object: str,
        *,
        action: str = "",
        why: str = "",
        when: str = "",
        intensity: float | None = None,
        confidence: float = 0.9,
        emotion: float | None = None,
    ) -> dict:
        """Record a confirmed fact, or reinforce it if it already exists.

        ``why`` is encouraged: an *object* says what happened, a *why* says what
        it meant — the meaning is what makes a memory worth keeping. ``emotion``
        (a valence in [-1, 1], optional) records *how it felt*; repeated emotional
        mentions settle the feeling, repeated flat ones keep it neutral (M1).
        Returns the stored (or reinforced) entry as a rendered view dict.
        """
        entry = self._build_entry(
            actor,
            object,
            action=action,
            why=why,
            when=when,
            intensity=intensity,
            confidence=confidence,
            default_intensity=self.cfg.default_intensity,
            emotion=emotion,
        )
        return self._write_or_reinforce(self.path, entry, emotion=emotion, confidence=confidence)

    def ask(
        self,
        actor: str,
        object: str,
        *,
        action: str = "",
        why: str = "",
        when: str = "",
        intensity: float | None = None,
        confidence: float = 0.9,
        emotion: float | None = None,
    ) -> dict:
        """Record a *grey-zone* fact — something not yet decided or only half-believed.

        Grey-zone facts live in a separate file and start at a lower intensity
        (``pending_intensity``) so an unconfirmed hunch fades faster than a sealed
        fact. ``emotion`` seeds its affect (M1) as for :meth:`add`. Promote one
        later with :meth:`confirm`. Returns the rendered entry.
        """
        entry = self._build_entry(
            actor,
            object,
            action=action,
            why=why,
            when=when,
            intensity=intensity,
            confidence=confidence,
            default_intensity=self.cfg.pending_intensity,
            emotion=emotion,
        )
        return self._write_or_reinforce(
            self.pending_path, entry, emotion=emotion, confidence=confidence
        )

    def confirm(self, id_or_keyword: str) -> list[dict]:
        """Promote matching grey-zone fact(s) into the confirmed store.

        Matches by exact id or keyword (a keyword may match several). Each match is
        moved out of the pending file and appended to the confirmed file. Returns
        the rendered list of promoted entries (empty if nothing matched).
        """
        target = str(id_or_keyword).lower()
        pending = _load_jsonl(self.pending_path)
        matched, remaining = [], []
        for e in pending:
            if self._is_active(e) and (_entry_id(e) == target or target in _entry_text(e)):
                matched.append(e)
            else:
                remaining.append(e)
        if not matched:
            return []
        now = datetime.now(timezone.utc)
        out = []
        for e in matched:
            e["_promoted_from_pending"] = True
            e["_promoted_at"] = _now_iso()
            _append_jsonl(self.path, e)
            out.append(self._render(e, now))
        _rewrite_jsonl(self.pending_path, remaining)
        return out

    def correct(
        self,
        id_or_keyword: str,
        *,
        object: str,
        action: str | None = None,
        why: str | None = None,
        when: str | None = None,
        where: str | None = None,
        intensity: float | None = None,
        confidence: float | None = None,
    ) -> dict:
        """Supersede an existing fact with a corrected version (the old one is kept).

        The old entry is marked ``_superseded_by`` (hidden from views but retained
        for audit), and a new entry inheriting its 5W1H — with the supplied fields
        overridden — is written. Returns the new entry rendered, or ``{}`` if no
        active match was found.
        """
        entries, idx = self._find_active(id_or_keyword, self.path)
        if idx < 0:
            return {}
        old = entries[idx]
        old_id = _entry_id(old)

        new_entry = dict(old)
        for k in (
            "_reinforce_count",
            "_last_reinforced",
            "_superseded_by",
            "_superseded_at",
            "_retracted",
            "_retracted_at",
        ):
            new_entry.pop(k, None)
        new_entry["ts"] = _now_iso()
        new_entry["supersedes"] = old_id
        new_entry.setdefault("what", {})
        if not isinstance(new_entry["what"], dict):
            new_entry["what"] = {"object": new_entry["what"]}
        new_entry["what"]["object"] = object
        if action is not None:
            new_entry["what"]["action"] = action
        if why is not None:
            new_entry["why"] = why
        if when is not None:
            new_entry["when"] = when
        if where is not None:
            new_entry["where"] = where
        if intensity is not None:
            new_entry["intensity"] = float(intensity)
        if confidence is not None:
            new_entry["confidence"] = float(confidence)

        new_id = _entry_id(new_entry)
        old["_superseded_by"] = new_id
        old["_superseded_at"] = _now_iso()
        entries.append(new_entry)
        _rewrite_jsonl(self.path, entries)
        return self._render(new_entry, datetime.now(timezone.utc))

    def retract(self, id_or_keyword: str) -> dict:
        """Mark a fact retracted (hidden from views but kept for audit).

        Returns the rendered retracted entry, or ``{}`` if no active match. Use
        this rather than deletion so a fact can be un-said without losing the
        record that it was ever held.
        """
        entries, idx = self._find_active(id_or_keyword, self.path)
        if idx < 0:
            return {}
        e = entries[idx]
        e["_retracted"] = True
        e["_retracted_at"] = _now_iso()
        _rewrite_jsonl(self.path, entries)
        return self._render(e, datetime.now(timezone.utc))

    # ------------------------------------------------------------------ #
    # Public API — read                                                  #
    # ------------------------------------------------------------------ #
    def search(self, keyword: str, *, actor: str | None = None) -> list[dict]:
        """Find visible/archived facts matching ``keyword`` (optionally by actor).

        A hit increments each matched fact's ``recalls`` and persists it: a fact
        you keep looking up decays more slowly ("used memory sticks"). Forgotten
        facts (below the archive floor) are not returned and not boosted. Results
        are sorted most-salient first. Returns rendered view dicts.
        """
        kw = str(keyword).lower()
        actor_l = actor.lower() if actor else None
        now = datetime.now(timezone.utc)
        confirmed = _load_jsonl(self.path)

        def matches(e: dict) -> bool:
            if not self._is_active(e):
                return False
            if kw not in _entry_text(e):
                return False
            if actor_l is not None:
                who = e.get("who") or {}
                a = (who.get("actor") if isinstance(who, dict) else who) or ""
                if actor_l not in a.lower():
                    return False
            return True

        hits = [
            e
            for e in confirmed
            if matches(e) and self._tier(self._current_intensity(e, now)) != "forgotten"
        ]

        # Recall feedback: bump recalls on confirmed hits and persist.
        if hits:
            hit_ids = {_entry_id(e) for e in hits}
            for e in confirmed:
                if _entry_id(e) in hit_ids and self._is_active(e):
                    e["recalls"] = int(e.get("recalls", 0)) + 1
                    e["_last_recalled"] = _now_iso()
            _rewrite_jsonl(self.path, confirmed)
            # Re-derive salience after the boost so the returned view is accurate.
            hits = [e for e in confirmed if _entry_id(e) in hit_ids and self._is_active(e)]

        rendered = [self._render(e, now) for e in hits]
        # Most salient first; ties broken by recency (newer ts first).
        rendered.sort(key=lambda r: (r["intensity"], r["ts"]), reverse=True)
        return rendered

    def recall(
        self,
        query: str,
        *,
        object_type: str | None = None,
        mood: float | None = None,
        scorer=None,
        limit: int = 8,
        congruence: float = 0.3,
    ) -> list[dict]:
        """Two-stage recall the agent *chooses* to call — never auto-injected (M4).

        Stage 1 is a cheap metadata prefilter: keep active, non-forgotten facts
        whose flattened text contains ``query`` (and whose action contains
        ``object_type`` if given), then hard-cap the candidate set so scoring stays
        bounded no matter how large the store grows. Stage 2 scores each candidate
        with ``scorer(query, fact_text) -> float`` (default: lexical token overlap;
        pass an embedding scorer for semantic recall), and — if ``mood`` (a
        valence) is given — adds a **mood-congruent** term (weighted by
        ``congruence``) so a low mood surfaces low memories and a bright one bright
        memories, the way human recall is state-dependent. A faint tie-break on
        salience keeps a vivid match ahead of a faded one.

        Like :meth:`search`, a returned fact's ``recalls`` is bumped ("used memory
        sticks"). Returns the top ``limit`` rendered view dicts, best first. This
        is a *tool*: what it returns, and whether to act on it, is the agent's —
        it injects nothing on its own.
        """
        kw = str(query).lower()
        ot = str(object_type).lower() if object_type else None
        now = datetime.now(timezone.utc)
        confirmed = _load_jsonl(self.path)

        # Stage 1 — metadata prefilter (cheap; keep the candidate set small).
        candidates = []
        for e in confirmed:
            if not self._is_active(e):
                continue
            if self._tier(self._current_intensity(e, now)) == "forgotten":
                continue
            if kw and kw not in _entry_text(e):
                continue
            if ot is not None:
                what = e.get("what") or {}
                action = (what.get("action") if isinstance(what, dict) else "") or ""
                if ot not in action.lower():
                    continue
            candidates.append(e)
        candidates = candidates[: max(limit * 8, 40)]
        if not candidates:
            return []

        score_fn = scorer if scorer is not None else _lexical_score

        # Stage 2 — score (+ optional mood-congruent re-rank + salience tie-break).
        scored = []
        for e in candidates:
            s = float(score_fn(kw, _entry_text(e)))
            if mood is not None:
                fv = self._affect_signals(e)["valence"]
                sim = 1.0 - abs(fv - float(mood)) / 2.0  # 1=lean matches mood, 0=opposite
                s = s * (1.0 - congruence) + sim * congruence
            s += 0.15 * self._current_intensity(e, now)
            scored.append((s, e))
        scored.sort(key=lambda t: t[0], reverse=True)
        ranked_ids = [_entry_id(e) for _, e in scored[:limit]]
        order = {eid: i for i, eid in enumerate(ranked_ids)}
        top_ids = set(ranked_ids)

        # Recall feedback: bump recalls on returned facts and persist.
        for e in confirmed:
            if _entry_id(e) in top_ids and self._is_active(e):
                e["recalls"] = int(e.get("recalls", 0)) + 1
                e["_last_recalled"] = _now_iso()
        _rewrite_jsonl(self.path, confirmed)
        out = [
            self._render(e, now)
            for e in confirmed
            if _entry_id(e) in top_ids and self._is_active(e)
        ]
        out.sort(key=lambda r: order.get(r["id"], 999))  # preserve the scored ranking
        return out

    def view(self, *, include_archived: bool = False) -> list[dict]:
        """Return the agent's currently-felt memories, decayed and sorted.

        By default only **visible** facts (salience >= ``visible_threshold``) are
        returned; pass ``include_archived=True`` to also include the dimmer
        archived tier. Forgotten and retracted/superseded facts are never shown.
        Sorted by salience (most vivid first), ties broken by recency. Returns
        rendered view dicts ready to hand to a renderer.
        """
        now = datetime.now(timezone.utc)
        wanted = {"visible", "archived"} if include_archived else {"visible"}
        out = []
        for e in _load_jsonl(self.path):
            if not self._is_active(e):
                continue
            current = self._current_intensity(e, now)
            if self._tier(current) in wanted:
                out.append(self._render(e, now))
        # Most vivid first; ties broken by recency (newer ts first).
        out.sort(key=lambda r: (r["intensity"], r["ts"]), reverse=True)
        return out

    # ------------------------------------------------------------------ #
    # Maintenance                                                        #
    # ------------------------------------------------------------------ #
    def compact(self) -> None:
        """Garbage-collect the store in place (safe to run periodically).

        Recomputes every confirmed fact's salience and rewrites the files:
        visible facts stay, archived facts are moved to the archived sibling file,
        and forgotten facts are dropped. Retracted/superseded entries are pruned.
        Internal bookkeeping fields are stripped on the way out so the on-disk
        files stay lean; recompaction is idempotent.
        """
        now = datetime.now(timezone.utc)

        def clean(e: dict) -> dict:
            return {k: v for k, v in e.items() if not k.startswith("_")}

        kept, to_archive = [], []
        for e in _load_jsonl(self.path):
            if not self._is_active(e):
                continue
            tier = self._tier(self._current_intensity(e, now))
            if tier == "visible":
                kept.append(clean(e))
            elif tier == "archived":
                to_archive.append(clean(e))
            # forgotten -> dropped
        _rewrite_jsonl(self.path, kept)
        if to_archive:
            existing_archive = _load_jsonl(self.archived_path)
            _rewrite_jsonl(self.archived_path, existing_archive + to_archive)

        # Prune the grey zone the same way (no archival tier for pending).
        pending_kept = [
            clean(e)
            for e in _load_jsonl(self.pending_path)
            if self._is_active(e) and self._tier(self._current_intensity(e, now)) != "forgotten"
        ]
        _rewrite_jsonl(self.pending_path, pending_kept)
