"""feltstate.memory.keyweb — memory as a web: word keys and judged edges, on the row.

A ledger is a list; a memory is a web. What turns one into the other is not a
vector index bolted on the side — it is two small facts *born on the record
itself*:

* ``keys`` — a handful of **single words** naming what the fact is about.
* ``relates`` — edges to other facts, each carrying *why* they are kin.

**One ledger.** Keys and edges live on the row, imprinted at birth or at
judgement time — never in a sidecar registry. A side registry starts as a
cache, quietly becomes the authority, and one day the row and the registry
disagree; whoever reads the wrong one recalls a memory that no longer exists.
If it cannot be read off the row, it is not part of the memory.

**Keys are words.** A key exists to *collide*: two memories that share a word
have met. A sentence-shaped key ("communication broke down between us") is a
fingerprint — unique, therefore useless; it will never match anything else. A
word-shaped key ("communication") is a meeting place. Split compound phrases
into their words; prefer the word that other memories would also choose. A key
that appears once in a lifetime is no key at all.

**New memories collide exactly once.** When a fact is born it plays protagonist
in one collision pass against the whole ledger — every older fact is a
candidate, forever; there is no recency window, because an old memory is not
less of a candidate for being old. After its one pass the newcomer sinks into
the pool and spends the rest of its life on the receiving end of other
newcomers' collisions. That single pass per fact is what keeps an all-history
candidate pool affordable.

**Admission is earned, not aged out.** An old candidate enters the pool when
its *birth* intensity, multiplied by how relevant it is right now (how many
keys it shares with the newcomer), clears a floor that rises with the age gap:

    strength = birth_intensity * relevance_mult(shared_keys)
    floor    = 1 - exp(-k * years_older_than_newcomer)

``relevance_mult`` approaches — never reaches — a ceiling of 2.0: relevance can
at most *double* a memory's reach, so no pile-up of shared keys can inflate a
faint memory into prominence. The floor uses the memory's **birth** intensity,
never its decayed present value: candidacy asks "how much did this matter?",
not "how warm is it today?" — decay governs what surfaces unprompted, the
lifecycle governs what dies; neither is admission's business.

**The library computes candidacy; a judge decides kinship.** Sharing words
makes two memories *candidates*, not kin. Whether they are truly related is a
judgement call — this module accepts any callable as the judge (an LLM pass, a
rules engine, a human). :class:`SharedKeyJudge` ships as a zero-dependency
reference judge for tests and offline use. With no judge, a digest returns the
candidate report and writes nothing: candidacy is arithmetic, kinship is an
opinion, and the library does not fake opinions.

Everything here is pure functions over plain dicts plus one convenience bridge
(:func:`digest_canon`) that runs the whole day-scope pass inside the canon's
own write lock. No network, no index files, no daemon.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from .canon import Canon, _entry_id, _load_jsonl, _now_iso, _parse_ts, _rewrite_jsonl, _write_lock

__all__ = [
    "KeyWebConfig",
    "SharedKeyJudge",
    "admission_floor",
    "collide",
    "day_digest",
    "digest_canon",
    "imprint_keys",
    "relevance_mult",
]


# --------------------------------------------------------------------------- #
# Configuration                                                               #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class KeyWebConfig:
    """Dials for the collision pass.

    ``floor_k`` calibrates how fast the admission floor rises with the age gap
    between newcomer and candidate. The default is tuned so a candidate ten
    years older than the newcomer needs birth-intensity x relevance ≈ 0.95 to
    enter the pool — reachable only by a near-permanent memory with strong
    relevance. There is deliberately no "0.99 tier": on a 0..1 intensity scale
    with a doubling ceiling, a floor that high admits nothing and only
    flatters the curve.
    """

    relevance_ceiling: float = 2.0  # shared keys can at most double reach
    floor_k: float = 0.2996  # ten years older -> floor ≈ 0.95
    max_key_chars: int = 16  # longer than this is a phrase, not a key
    min_shared_keys: int = 1  # collisions below this never make candidacy


_DEFAULT = KeyWebConfig()

# Keys must be single word-like tokens: no whitespace, no clause punctuation.
_KEY_FORBIDDEN = re.compile(r"[\s,;:.!?，。；：！？]")


# --------------------------------------------------------------------------- #
# The two curves                                                              #
# --------------------------------------------------------------------------- #
def relevance_mult(shared: int, ceiling: float = 2.0) -> float:
    """How much ``shared`` key collisions multiply a candidate's reach.

    ``ceiling - (ceiling - 1)/shared``: one shared key multiplies by 1.0 (no
    boost — merely touching is not relevance), two by 1.5, four by 1.75,
    asymptotically approaching — never reaching — the ceiling. Relevance can
    at most *double* a memory; it cannot mint importance that was never there.
    """
    if shared < 1:
        return 0.0
    if ceiling <= 1.0:
        raise ValueError(f"ceiling must be > 1, got {ceiling}")
    return ceiling - (ceiling - 1.0) / shared


def admission_floor(extra_years: float, k: float = _DEFAULT.floor_k) -> float:
    """The intensity bar a candidate must clear, given how much *older* it is.

    ``1 - exp(-k * extra_years)``: zero for a same-day peer, rising smoothly —
    with the default ``k``, roughly 0.26 at one year, 0.78 at five, 0.95 at
    ten. A candidate *younger* than the newcomer pays no floor (``extra_years``
    clamps at 0): the bar prices the reach *back* in time, not forward.
    """
    if k <= 0:
        raise ValueError(f"k must be > 0, got {k}")
    return 1.0 - math.exp(-k * max(0.0, extra_years))


# --------------------------------------------------------------------------- #
# Imprinting keys                                                             #
# --------------------------------------------------------------------------- #
def imprint_keys(entry: dict, keys: Iterable[str], cfg: KeyWebConfig | None = None) -> list[str]:
    """Imprint word keys on a ledger row, in place; return what was accepted.

    Enforces the words-only rule mechanically: a key containing whitespace or
    clause punctuation, or longer than ``max_key_chars``, is dropped — those
    are phrases wearing a key's clothes, and a phrase never collides. Keys are
    deduplicated preserving order. An entry with no surviving keys is left
    untouched (no empty ``keys`` field is written; absence means "not yet
    keyed", which the digest can distinguish from "keyed and found nothing").
    """
    if cfg is None:
        cfg = _DEFAULT
    accepted: list[str] = []
    seen: set[str] = set()
    for raw in keys:
        k = str(raw).strip()
        if not k or len(k) > cfg.max_key_chars or _KEY_FORBIDDEN.search(k):
            continue
        if k.lower() in seen:
            continue
        seen.add(k.lower())
        accepted.append(k)
    if accepted:
        entry["keys"] = accepted
    return accepted


def _keys_of(entry: Mapping) -> list[str]:
    ks = entry.get("keys")
    if not isinstance(ks, (list, tuple)):
        return []
    return [str(k) for k in ks if str(k).strip()]


def _age_years_between(newer: Mapping, older: Mapping) -> float:
    """How many years *older* the candidate is than the newcomer (>= 0).

    Both sides read event time first (``valid_at``), falling back to record
    time (``ts``) — candidacy prices the gap between when things *happened*.
    Unparseable stamps yield 0.0: no evidence of an age gap, no floor.
    """
    tn = _parse_ts(str(newer.get("valid_at") or newer.get("ts") or ""))
    to = _parse_ts(str(older.get("valid_at") or older.get("ts") or ""))
    if tn is None or to is None:
        return 0.0
    return max(0.0, (tn - to).total_seconds() / (365.25 * 86400.0))


# --------------------------------------------------------------------------- #
# The collision pass                                                          #
# --------------------------------------------------------------------------- #
def collide(
    newcomer: Mapping,
    ledger: Sequence[Mapping],
    cfg: KeyWebConfig | None = None,
) -> list[dict]:
    """One protagonist pass: the newcomer's keys against the whole ledger.

    Returns a full audit trail — one report per touched candidate, admitted or
    not — sorted by shared-key count, then strength::

        {"entry", "shared": [keys], "hits": n,
         "strength": birth_intensity * relevance_mult(n),
         "floor": admission_floor(extra_years),
         "admitted": strength >= floor and n >= min_shared_keys}

    Only *older* rows (event time at or before the newcomer's) are candidates:
    the web is built looking backward, and tomorrow's facts will look back at
    today's in their own pass. The newcomer itself and rows without keys are
    skipped. Nothing is written; this is arithmetic, not judgement.
    """
    if cfg is None:
        cfg = _DEFAULT
    my_keys = {k.lower() for k in _keys_of(newcomer)}
    my_id = _entry_id(dict(newcomer))
    out: list[dict] = []
    if not my_keys:
        return out
    for cand in ledger:
        if _entry_id(dict(cand)) == my_id:
            continue
        if _age_years_between(cand, newcomer) > 0.0:
            continue  # candidate is *newer* than the protagonist: not its job
        shared = [k for k in _keys_of(cand) if k.lower() in my_keys]
        if not shared:
            continue
        hits = len(shared)
        birth = float(cand.get("intensity", 0.0) or 0.0)
        strength = birth * relevance_mult(hits, cfg.relevance_ceiling)
        floor = admission_floor(_age_years_between(newcomer, cand), cfg.floor_k)
        out.append(
            {
                "entry": cand,
                "shared": shared,
                "hits": hits,
                "strength": round(strength, 4),
                "floor": round(floor, 4),
                "admitted": hits >= cfg.min_shared_keys and strength >= floor,
            }
        )
    out.sort(key=lambda r: (-r["hits"], -r["strength"]))
    return out


# --------------------------------------------------------------------------- #
# Judges                                                                      #
# --------------------------------------------------------------------------- #
# A judge sees (newcomer, candidate, shared_keys) and returns why they are kin
# (one short clause) or None for "merely touching". Any callable fits; wire an
# LLM pass here in production.
Judge = Callable[[Mapping, Mapping, Sequence[str]], "str | None"]


class SharedKeyJudge:
    """Zero-dependency reference judge: kinship = enough shared keys.

    Honest about what it is — a lexical stand-in so the digest is runnable
    with no model attached. It cannot tell "the same rent dispute" from "both
    mention rent"; a real deployment should put a model or a human here.
    """

    def __init__(self, min_shared: int = 2):
        self.min_shared = int(min_shared)

    def __call__(self, newcomer: Mapping, candidate: Mapping, shared: Sequence[str]) -> str | None:
        if len(shared) >= self.min_shared:
            return "shared keys: " + ", ".join(str(s) for s in shared[:4])
        return None


# --------------------------------------------------------------------------- #
# The day-scope digest                                                        #
# --------------------------------------------------------------------------- #
def day_digest(
    new_entries: Sequence[dict],
    ledger: Sequence[dict],
    judge: Judge | None = None,
    cfg: KeyWebConfig | None = None,
) -> dict:
    """Run each new entry's one protagonist pass; write judged edges on both rows.

    ``new_entries`` are today's newborns (must be items of ``ledger`` — edges
    are imprinted in place on those same dicts). Each newcomer collides once
    against the whole ledger; admitted candidates go to the ``judge``; a
    verdict becomes an edge on *both* rows::

        {"to": <other id>, "why": <judge's clause>, "ts": <now, host-local>}

    With ``judge=None`` nothing is written and the report carries the admitted
    candidates only — candidacy is arithmetic, kinship is an opinion, and this
    library does not fake opinions.

    Returns ``{"passes": n, "candidates": m, "edges": [...]}, `` with one
    report row per newcomer under ``"detail"``.
    """
    if cfg is None:
        cfg = _DEFAULT
    edges: list[dict] = []
    detail: list[dict] = []
    n_cand = 0
    for nb in new_entries:
        reports = collide(nb, ledger, cfg)
        admitted = [r for r in reports if r["admitted"]]
        n_cand += len(admitted)
        made: list[dict] = []
        if judge is not None:
            nb_id = _entry_id(nb)
            existing = {e.get("to") for e in nb.get("relates", []) if isinstance(e, Mapping)}
            for r in admitted:
                cand = r["entry"]
                why = judge(nb, cand, r["shared"])
                if not why:
                    continue
                cand_id = _entry_id(dict(cand))
                if cand_id in existing:
                    continue
                ts = _now_iso()
                edge_out = {"to": cand_id, "why": str(why), "ts": ts}
                edge_back = {"to": nb_id, "why": str(why), "ts": ts}
                nb.setdefault("relates", []).append(edge_out)
                if isinstance(cand, dict):
                    back = cand.setdefault("relates", [])
                    if all(e.get("to") != nb_id for e in back if isinstance(e, Mapping)):
                        back.append(edge_back)
                made.append(edge_out)
        edges.extend(made)
        detail.append(
            {
                "id": _entry_id(nb),
                "keys": _keys_of(nb),
                "admitted": len(admitted),
                "edges": len(made),
                "candidates": [
                    {k: r[k] for k in ("shared", "hits", "strength", "floor", "admitted")}
                    for r in reports
                ],
            }
        )
    return {"passes": len(new_entries), "candidates": n_cand, "edges": edges, "detail": detail}


def digest_canon(
    canon: Canon,
    new_ids: Sequence[str],
    judge: Judge | None = None,
    cfg: KeyWebConfig | None = None,
) -> dict:
    """Day-scope digest over a live :class:`Canon`, transactionally.

    Loads the confirmed store under its own write lock, runs :func:`day_digest`
    with the rows whose ids are in ``new_ids`` as protagonists, and rewrites the
    file only if edges were actually made — the whole read-modify-write happens
    inside one lock so a concurrent append cannot be erased by a stale
    snapshot. Rows living in the archived sidecar are candidates too, read-only
    (their reciprocal edges are written when *they* are protagonists; an
    archived fact never is).
    """
    wanted = {str(i) for i in new_ids}
    with _write_lock(canon.path):
        main = _load_jsonl(canon.path)
        archived = _load_jsonl(canon.archived_path)
        ledger = main + [a for a in archived if _entry_id(a) not in {_entry_id(m) for m in main}]
        news = [e for e in main if _entry_id(e) in wanted]
        report = day_digest(news, ledger, judge, cfg)
        if report["edges"]:
            _rewrite_jsonl(canon.path, main)
    return report
