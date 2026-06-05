"""feltstate.memory.skill — a human-rated, self-growing skill region inside canon.

A *skill* is an ordinary :mod:`~feltstate.memory.canon` 5W1H entry tagged
``region == "skill"`` plus a parallel ``skill`` sub-dict holding the agent's
real-use track record: **human 1/2/3 ratings** (1 = lousy, 2 = ok, 3 = excellent).
Skills share the same three jsonl files as facts (demarcated by the tag, not a
separate store), so decay, dedup and ``compact`` all apply — but verification
attaches **only** to this region. Fact/emotion entries are never scored or gated.

The signal is the **human verdict in real use**, decided after a task completes —
because what matters is whether a skill was *useful*, which "did it execute" can't
tell you and an emotion reading can't reliably tell you either (both were tried
and rejected). A rating is given per *task* and shared across the skills that task
used, so a noisy per-task score becomes a clean per-skill signal over volume.

Lifecycle (all the agent's own tools + the human's ratings; no daemon, no
behind-the-back pass):

* Every skill is born **grey** (provisional, low-confidence — :func:`add_skill`).
  The grey zone decays *slowly* (a long lease to be tried and rated).
* :func:`recall_skills` selects **probabilistically, weighted by rating**
  (explore/exploit): proven skills win most, but a low one keeps a non-zero
  chance to be re-tried and redeem itself — so no skill monopolises and a
  newly-good one can rise.
* :func:`record_rating` / :func:`record_task_rating` fold a human rating in.
  Three "3" ratings with no "1" → **auto-promote** to the confirmed store; enough
  "1"s → **retire**; a mixed record stays grey, flagged unstable.
* :func:`review_skills` lets the agent see its whole library during introspection
  to tidy it (merge, retire via ``Canon.retract``, ratify via :func:`ratify_skill`).
* :class:`RatingGate` rate-limits the ask so consecutive tasks never nag the human.

Hard lines (PHILOSOPHY.md): tool-not-controller (everything produces state the
agent reads), ground-truth-not-self-report (the rating is the *human's*, never the
reply model's), identity-merge / no attention skew (skills are retrieve-on-demand,
never in view()/render(), never permanent).
"""

from __future__ import annotations

import json
import random
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

from ..config import MemoryConfig
from .canon import (
    _entry_id,
    _entry_text,
    _lexical_score,
    _load_jsonl,
    _now_iso,
    _parse_ts,
    _rewrite_jsonl,
)

__all__ = [
    "SKILL_REGION",
    "SKILL_ACTION",
    "OBSERVED_SOURCES",
    "add_skill",
    "record_rating",
    "record_task_rating",
    "recall_skills",
    "review_skills",
    "ratify_skill",
    "rating_priority",
    "RatingGate",
    "SkillRatifier",
]

SKILL_REGION = "skill"
SKILL_ACTION = "procedure"

# Who may move a skill's rating. The reply model is NOT here: it cannot grade its
# own work (a self-reported "that went well" is a structural no-op). "human" is the
# real-use verdict (the gold signal); the others are objective ground-truth that
# can only ever lower a skill (a tool error / a user correction is a "1").
OBSERVED_SOURCES = frozenset({"human", "user_correction", "tool_exit", "verifier", "harness"})

# A 1/2/3 rating maps to a [0,1] quality value.
_RATING_VALUE = {1: 0.0, 2: 0.5, 3: 1.0}


def _new_skill_meta(cfg) -> dict:
    """A fresh skill sub-dict: no ratings yet, utility at the prior seed."""
    return {
        "n1": 0,  # lousy ratings
        "n2": 0,  # ok ratings
        "n3": 0,  # excellent ratings
        "utility": round(float(cfg.skill_seed), 4),
        "last_rating_ts": "",
        "retired": False,
    }


def _utility(meta: dict, cfg) -> float:
    """Shrunk mean of the 1/2/3 ratings in [0,1] — an unrated skill sits at the
    prior seed and earns its way up with real ratings (the prior gives inertia, so
    one stray rating can't swing it)."""
    n1, n2, n3 = int(meta.get("n1", 0)), int(meta.get("n2", 0)), int(meta.get("n3", 0))
    n = n1 + n2 + n3
    sum_v = _RATING_VALUE[1] * n1 + _RATING_VALUE[2] * n2 + _RATING_VALUE[3] * n3
    pn = float(cfg.skill_prior_n)
    return round((sum_v + float(cfg.skill_seed) * pn) / (n + pn), 4)


def _is_skill(entry: dict) -> bool:
    return (entry.get("region") or "fact") == SKILL_REGION


def _skill_view(canon, entry: dict, *, proven: bool) -> dict:
    """Render a skill as a view dict: the canon projection plus the skill signals."""
    now = datetime.now(timezone.utc)
    r = canon._render(entry, now)
    meta = entry.get("skill") or {}
    r["region"] = SKILL_REGION
    r["how"] = entry.get("how", "")
    r["n1"] = int(meta.get("n1", 0))
    r["n2"] = int(meta.get("n2", 0))
    r["n3"] = int(meta.get("n3", 0))
    r["ratings"] = r["n1"] + r["n2"] + r["n3"]
    r["utility"] = _utility(meta, canon.cfg)
    r["retired"] = bool(meta.get("retired", False))
    r["proven"] = bool(proven)
    # A grey (unproven) skill is low-confidence: the agent must re-confirm / verify
    # before relying on it, and a mixed record (any "1" among "3"s) is unstable.
    r["must_confirm"] = not bool(proven)
    r["unstable"] = r["n3"] >= canon.cfg.skill_promote_excellent_count and r["n1"] > 0
    return r


def _find_active_skill(canon, target):
    """Locate an active skill by exact id then keyword, confirmed store first then
    grey. Returns ``(path, entries, idx)``; ``(None, [], -1)`` if none matched."""
    t = str(target).lower()
    for path in (canon.path, canon.pending_path):
        entries = _load_jsonl(path)
        for i, e in enumerate(entries):
            if not canon._is_active(e) or not _is_skill(e):
                continue
            if _entry_id(e) == t or t in _entry_text(e):
                return path, entries, i
    return None, [], -1


# --------------------------------------------------------------------------- #
# Write — add a skill (grey by default)                                       #
# --------------------------------------------------------------------------- #
def add_skill(
    canon,
    actor: str,
    capability: str,
    *,
    why: str = "",
    how: str = "",
    confidence: float = 0.9,
    grey: bool = True,
    cfg=None,
    _seed_rating: tuple | None = None,
) -> dict:
    """Record a capability the agent has (or might have) — a tool the agent calls.

    ``grey=True`` (default) writes a low-confidence candidate into the grey zone;
    it must be tried, rated, and earn promotion. ``grey=False`` writes a confirmed
    skill, but never permanent (base intensity hard-capped below ``permanent_above``
    so no skill becomes always-resident). ``why`` is what the skill is for; ``how``
    is the optional procedure body (kept on the entry and matchable). Returns the
    rendered skill view.
    """
    cfg = cfg or canon.cfg
    path = canon.pending_path if grey else canon.path
    base = cfg.pending_intensity if grey else min(cfg.default_intensity, cfg.skill_base_cap)
    entry = canon._build_entry(
        actor,
        capability,
        action=SKILL_ACTION,
        why=why,
        intensity=base,
        confidence=confidence,
        default_intensity=base,
        region=SKILL_REGION,
    )
    if how:
        entry["how"] = how
    meta = _new_skill_meta(cfg)
    if _seed_rating:
        rating, ts = _seed_rating
        meta[f"n{int(rating)}"] += 1
        meta["last_rating_ts"] = ts
    meta["utility"] = _utility(meta, cfg)
    entry["skill"] = meta
    canon._write_or_reinforce(path, entry)
    return _skill_view(canon, entry, proven=not grey)


# --------------------------------------------------------------------------- #
# The human-rating gate — the only mutator of a skill's standing               #
# --------------------------------------------------------------------------- #
def record_rating(
    canon,
    skill_id_or_trigger: str,
    rating: int,
    *,
    source: str = "human",
    actor: str = "self",
    note: str = "",
    cfg=None,
    now: datetime | None = None,
) -> dict:
    """Fold one **observed** rating (1/2/3) into a skill, and maybe promote/retire.

    The rating is the human's real-use verdict (1 lousy / 2 ok / 3 excellent), given
    after a task — NOT the agent grading itself: a ``source`` outside
    :data:`OBSERVED_SOURCES` (e.g. the reply model) is a structural no-op.

    * **3 ratings of "3" and no "1"** → auto-promote to the confirmed store (stable
      excellence — the count-based promotion, now backed by real human ratings).
    * **enough "1"s** (``skill_retire_bad_count``) → retire (lousy in real use).
    * a mixed record (some "3", some "1") stays grey, flagged ``unstable``.
    * no matching skill → a fresh grey candidate is born seeded with this rating.

    A rating refreshes the skill's recency (a real use), so an actively-rated skill
    does not fade. Returns the rendered skill view (``{}`` on a no-op).
    """
    cfg = cfg or canon.cfg
    if int(rating) not in _RATING_VALUE:
        raise ValueError("rating must be 1, 2 or 3")
    rating = int(rating)
    if source not in OBSERVED_SOURCES:
        return {}  # ground-truth gate: a self-reported source moves nothing

    now_dt = now or datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()
    path, entries, idx = _find_active_skill(canon, skill_id_or_trigger)
    if idx < 0:
        return add_skill(
            canon,
            actor,
            str(skill_id_or_trigger),
            why=note,
            grey=True,
            cfg=cfg,
            _seed_rating=(rating, now_iso),
        )

    e = entries[idx]
    meta = e.setdefault("skill", _new_skill_meta(cfg))
    meta[f"n{rating}"] = int(meta.get(f"n{rating}", 0)) + 1
    meta["last_rating_ts"] = now_iso
    meta["utility"] = _utility(meta, cfg)
    e["ts"] = now_iso  # a rating is a real use -> refresh recency
    e["_reinforce_count"] = int(e.get("_reinforce_count", 0)) + 1
    e["_last_reinforced"] = now_iso

    n1, n3 = int(meta["n1"]), int(meta["n3"])
    on_grey = path == canon.pending_path
    retire = n1 >= cfg.skill_retire_bad_count
    promote = on_grey and (not retire) and n3 >= cfg.skill_promote_excellent_count and n1 == 0

    if retire:
        meta["retired"] = True
        e["_retracted"] = True
        e["_retracted_at"] = now_iso
        e["invalid_at"] = now_iso

    _rewrite_jsonl(path, entries)
    if promote:
        canon.confirm(_entry_id(e))  # move grey candidate -> confirmed (meta preserved)
        return _skill_view(canon, e, proven=True)
    return _skill_view(canon, e, proven=(path == canon.path))


def record_task_rating(
    canon,
    skill_ids,
    rating: int,
    *,
    source: str = "human",
    cfg=None,
    now: datetime | None = None,
) -> list[dict]:
    """Apply ONE task rating to every skill the task used (credit is shared).

    The human scores a completed task once (not each skill); that score lands on
    each skill that task drew on. Over many tasks a skill that keeps turning up in
    good tasks rises and one that keeps turning up in bad tasks sinks — per-task
    noise averaging into per-skill signal. Returns the updated skill views.
    """
    cfg = cfg or canon.cfg
    now = now or datetime.now(timezone.utc)
    out = []
    for sid in skill_ids or []:
        r = record_rating(canon, sid, rating, source=source, cfg=cfg, now=now)
        if r:
            out.append(r)
    return out


# --------------------------------------------------------------------------- #
# Read — probabilistic, rating-weighted selection (explore/exploit)            #
# --------------------------------------------------------------------------- #
def recall_skills(
    canon,
    query: str,
    *,
    limit: int = 5,
    include_gray: bool = True,
    scorer=None,
    cfg=None,
    rng=None,
) -> list[dict]:
    """Pick skills for the current need — **probabilistically, weighted by rating**.

    Among the skills relevant to ``query``, draws ``limit`` of them with probability
    rising in their rating (a confirmed skill is boosted ``skill_select_promoted_boost``
    ×; every skill keeps a non-zero ``skill_select_floor`` so a low one can still be
    re-tried and redeem itself). ``skill_select_temperature`` tunes the steepness
    (low = exploit the proven, high = explore more). This is the agent's tool — it
    selects, never auto-injects. ``include_gray=False`` restricts to confirmed
    skills. Retired skills are never returned; a returned skill's ``recalls`` is
    bumped (recall frequency never feeds the rating). Pass a seeded ``rng`` for
    reproducibility.
    """
    cfg = cfg or canon.cfg
    rng = rng or random.Random()
    now = datetime.now(timezone.utc)
    kw = str(query).lower()
    score_fn = scorer if scorer is not None else _lexical_score

    sources = [(canon.path, True)]
    if include_gray:
        sources.append((canon.pending_path, False))

    pool = []  # (weight, entry, path, proven)
    temp = max(1e-6, float(cfg.skill_select_temperature))
    for path, proven in sources:
        for e in _load_jsonl(path):
            if not canon._is_active(e) or not _is_skill(e):
                continue
            if (e.get("skill") or {}).get("retired"):
                continue
            if canon._tier(canon._current_intensity(e, now)) == "forgotten":
                continue
            if kw and kw not in _entry_text(e):
                continue
            if kw and float(score_fn(kw, _entry_text(e))) <= 0.0:
                continue
            w = max(cfg.skill_select_floor, _utility(e.get("skill") or {}, cfg))
            if proven:
                w *= cfg.skill_select_promoted_boost
            w = w ** (1.0 / temp)  # temperature: sharpen (low) / flatten (high)
            pool.append((w, e, path, proven))
    # Same bound as Canon.recall: cap the candidate set so scoring stays bounded
    # however large the library grows. With slow decay pruning unused skills, the
    # active set stays small (dozens, not thousands), so this rarely even bites —
    # which is exactly why the retrieval-scale collapse never reaches us.
    pool = pool[: max(limit * 8, 40)]
    if not pool:
        return []

    # Weighted draw without replacement.
    chosen = []
    cand = list(pool)
    for _ in range(min(limit, len(cand))):
        total = sum(w for w, *_ in cand)
        if total <= 0.0:
            i = rng.randrange(len(cand))
        else:
            r = rng.random() * total
            acc = 0.0
            i = len(cand) - 1
            for j, (w, *_rest) in enumerate(cand):
                acc += w
                if acc >= r:
                    i = j
                    break
        chosen.append(cand.pop(i))

    # Recall feedback: bump recalls on chosen, per source file.
    by_path: dict = {}
    for _w, e, path, _p in chosen:
        by_path.setdefault(path, set()).add(_entry_id(e))
    for path, ids in by_path.items():
        allrows = _load_jsonl(path)
        for e in allrows:
            if _entry_id(e) in ids and canon._is_active(e):
                e["recalls"] = int(e.get("recalls", 0)) + 1
                e["_last_recalled"] = _now_iso()
        _rewrite_jsonl(path, allrows)

    return [_skill_view(canon, e, proven=proven) for _w, e, _path, proven in chosen]


def review_skills(canon, *, limit: int = 50, include_gray: bool = True, cfg=None) -> list[dict]:
    """Read-only overview of the whole skill library, for introspection.

    Lists confirmed (and grey) skills sorted by utility then recency — does NOT bump
    recalls (reviewing is reflection, not use). This is what the agent reads when it
    tidies its own skills during introspection: merge duplicates (``add_skill`` a
    clean one + ``Canon.retract`` the messy), retire dead ones, ratify the
    ``unstable`` greys it has decided to trust. The *when* and the *judgement* are
    the agent's; this only hands it the list.
    """
    cfg = cfg or canon.cfg
    now = datetime.now(timezone.utc)
    sources = [(canon.path, True)]
    if include_gray:
        sources.append((canon.pending_path, False))
    items = []
    for path, proven in sources:
        for e in _load_jsonl(path):
            if not canon._is_active(e) or not _is_skill(e):
                continue
            if (e.get("skill") or {}).get("retired"):
                continue
            items.append((e, proven))
    items.sort(
        key=lambda it: (
            _utility(it[0].get("skill") or {}, cfg),
            canon._current_intensity(it[0], now),
        ),
        reverse=True,
    )
    return [_skill_view(canon, e, proven=proven) for e, proven in items[:limit]]


def rating_priority(canon, *, limit: int = 10, cfg=None) -> list[dict]:
    """Which grey skills most warrant a human rating right now (active learning).

    Orders unproven grey skills by how much a rating would buy: fewest ratings first
    (least evidence), then those closest to the promote/retire boundary. The overlay
    can use this to spend a scarce rating request where it resolves the most
    uncertainty. Returns grey skill views, most-worth-asking first.
    """
    cfg = cfg or canon.cfg
    now = datetime.now(timezone.utc)
    items = []
    for e in _load_jsonl(canon.pending_path):
        if not canon._is_active(e) or not _is_skill(e):
            continue
        if (e.get("skill") or {}).get("retired"):
            continue
        if canon._tier(canon._current_intensity(e, now)) == "forgotten":
            continue
        m = e.get("skill") or {}
        n = int(m.get("n1", 0)) + int(m.get("n2", 0)) + int(m.get("n3", 0))
        # distance to the excellent-promote threshold (smaller = closer = ask sooner)
        gap = max(0, cfg.skill_promote_excellent_count - int(m.get("n3", 0)))
        items.append(((n, gap), e))
    items.sort(key=lambda it: it[0])  # fewest ratings first, then closest to promote
    return [_skill_view(canon, e, proven=False) for _k, e in items[:limit]]


def ratify_skill(canon, candidate_id: str, *, judge: SkillRatifier, cfg=None) -> bool:
    """Promote a grey skill candidate to confirmed — by an explicit introspection
    *decision* (for the cases auto-promotion does not cover: an ``unstable`` skill
    the agent has nonetheless decided to trust, or one it wants to keep before it
    reaches three "3"s). The track record rides in the view as information the judge
    weighs. On a keep, ``canon.confirm`` moves it. Returns whether it was promoted.
    """
    cfg = cfg or canon.cfg
    path, entries, idx = _find_active_skill(canon, candidate_id)
    if idx < 0 or path != canon.pending_path:
        return False
    e = entries[idx]
    if not judge.ratify(_skill_view(canon, e, proven=False)):
        return False
    promoted = canon.confirm(_entry_id(e))
    return bool(promoted)


# --------------------------------------------------------------------------- #
# Rating gate — never nag (10-min cooldown + daily cap)                        #
# --------------------------------------------------------------------------- #
class RatingGate:
    """Rate-limits the human rating request so consecutive tasks never nag.

    The overlay calls :meth:`allow` before showing the 1/2/3 widget; if it returns
    False, it just doesn't ask this time (the skills accrue evidence later). On a
    shown ask it calls :meth:`stamp`. State (last-ask time + per-day count) persists
    in a small JSON sidecar so the cooldown survives restarts.
    """

    def __init__(self, state_path: str | Path, cfg: MemoryConfig | None = None):
        self.path = Path(state_path)
        self.cfg = cfg if cfg is not None else MemoryConfig()
        self.last_ts = ""
        self.day = ""
        self.count = 0
        if self.path.is_file():
            try:
                d = json.loads(self.path.read_text(encoding="utf-8"))
                self.last_ts = str(d.get("last_ts", "") or "")
                self.day = str(d.get("day", "") or "")
                self.count = int(d.get("count", 0) or 0)
            except (OSError, ValueError):
                pass

    def allow(self, now: datetime) -> bool:
        """May we ask for a rating now? False if inside the cooldown or over the
        daily cap. Read-only — call :meth:`stamp` when an ask is actually shown."""
        day = now.date().isoformat()
        count = self.count if day == self.day else 0
        if count >= int(self.cfg.rating_daily_cap):
            return False
        last = _parse_ts(self.last_ts) if self.last_ts else None
        if last is not None and (now - last).total_seconds() < float(self.cfg.rating_cooldown_s):
            return False
        return True

    def stamp(self, now: datetime) -> None:
        """Record that a rating request was shown now (resets the cooldown)."""
        day = now.date().isoformat()
        if day != self.day:
            self.day = day
            self.count = 0
        self.count += 1
        self.last_ts = now.isoformat()
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps({"last_ts": self.last_ts, "day": self.day, "count": self.count}),
            encoding="utf-8",
        )
        tmp.replace(self.path)


# --------------------------------------------------------------------------- #
# Model seam (the introspection prompt goes here) — stub, no default           #
# --------------------------------------------------------------------------- #
class SkillRatifier(ABC):
    """The agent's introspection deciding keep/drop a grey candidate. ``True`` → it
    is confirmed. Pure judgement; writes nothing. The prompt is the caller's."""

    @abstractmethod
    def ratify(self, candidate: dict) -> bool:  # pragma: no cover - interface
        ...
