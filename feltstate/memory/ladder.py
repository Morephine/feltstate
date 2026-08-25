"""feltstate.memory.ladder — the crystallisation ladder: day → week → month → year.

:func:`.lifecycle.smelt` forges one crystal and deliberately leaves the
scheduling to the caller. This module is that caller, shipped: the same
furnace climbed rung by rung, only the dials changing.

* **Casting.** Facts whose *birth* intensity clears a floor are cast into
  day crystals — one each, sealed with a fingerprint whose pointer hashes the
  fact's own text, so every rung above stays drillable to the words below.
* **Climbing.** Live, unabsorbed crystals of a tier are clustered by their
  most-shared key; when a cluster reaches the tier's batch size, a summariser
  (your seat — an LLM in production, a joiner offline) writes the fused text,
  the furnace seals it one tier up, and the crystal takes a small climb bonus:
  surviving the melt is itself evidence of mattering.
* **Absorption, derived.** Melted members sink. No ``absorbed`` flag is ever
  written: a crystal is absorbed iff some higher crystal lists its ``mid`` in
  ``src_ids`` — read off the store, never stored beside it. The same goes for
  "already cast": a fact is cast iff a day crystal cites it. What can be read
  from existing data is never written as new state, so the two can never
  disagree.
* **Heat, computed.** A crystal's current heat is a pure function of its born
  heat and age (per-tier half-life): nothing ticks, nothing writes, and a
  crystal too cold for its tier's admission bar simply never climbs — the
  ladder is how memories earn longevity, not a conveyor that moves everything.

The store is a caller-owned JSONL, one crystal per line, append-only here
(the reaper owns deletion). Everything is zero-LLM except the summariser
seat, which receives words and returns words.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .lifecycle.smelt import SmeltConfig, smelt

__all__ = [
    "DEFAULT_LADDER",
    "TierDial",
    "absorbed_mids",
    "cast_day_crystals",
    "cast_fact_ids",
    "cluster_by_key",
    "heat_now",
    "ladder_pass",
    "load_crystals",
]


# --------------------------------------------------------------------------- #
# Dials                                                                       #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TierDial:
    """One rung: what it is called, where it melts to, and what it takes.

    ``batch`` is how many clustered members fuse into one crystal above;
    ``min_heat`` is the admission bar (current heat, not born heat — a rung
    admits what still matters *now*); ``half_life_days`` drives the pure
    ageing curve; ``climb_bonus`` is added to the fused crystal's born heat —
    the reference deployment's "+0.08 per rung climbed".
    """

    name: str
    melts_to: str
    batch: int
    min_heat: float
    half_life_days: float = 45.0
    climb_bonus: float = 0.08


DEFAULT_LADDER: tuple[TierDial, ...] = (
    TierDial("day", "week", batch=12, min_heat=0.55, half_life_days=30.0),
    TierDial("week", "month", batch=8, min_heat=0.60, half_life_days=90.0),
    TierDial("month", "year", batch=6, min_heat=0.65, half_life_days=240.0),
)

# The summariser seat: receives the member texts, returns the fused text.
Summarizer = Callable[[Sequence[str]], str]


def _join_summarizer(texts: Sequence[str]) -> str:
    """The offline seat filler: verbatim join, consistent by construction."""
    return "; ".join(t.strip().rstrip(".") for t in texts if t.strip())


# --------------------------------------------------------------------------- #
# Store — caller-owned JSONL, append-only from here                           #
# --------------------------------------------------------------------------- #
def load_crystals(path: str | Path) -> list[dict]:
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return out


def _append(path: str | Path, crystal: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(crystal, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- #
# Derived state — read, never written                                         #
# --------------------------------------------------------------------------- #
def cast_fact_ids(crystals: Sequence[Mapping]) -> set[str]:
    """Facts already cast: every id a day crystal cites. Derived, not marked."""
    out: set[str] = set()
    for c in crystals:
        if c.get("tier") == "day":
            out.update(str(s) for s in c.get("src_ids") or [])
    return out


def absorbed_mids(crystals: Sequence[Mapping]) -> set[str]:
    """Crystals already melted upward: cited as ``src_ids`` by any crystal."""
    out: set[str] = set()
    for c in crystals:
        if c.get("tier") != "day":
            out.update(str(s) for s in c.get("src_ids") or [])
    return out


def heat_now(crystal: Mapping, now: datetime, half_life_days: float) -> float:
    """Current heat: born heat halved every ``half_life_days``. Pure, no writes."""
    born = float(crystal.get("heat") or 0.0)
    try:
        ts = datetime.fromisoformat(str(crystal.get("ts")).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return born
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    days = max(0.0, (now - ts).total_seconds() / 86400.0)
    if half_life_days <= 0:
        return born
    return born * math.pow(0.5, days / half_life_days)


def cluster_by_key(members: Sequence[Mapping]) -> list[list[Mapping]]:
    """Greedy theme clusters: the most-shared key claims its members first.

    Keys were single words chosen to collide; here the collision pays off a
    second time. Members with no key shared with anyone form their own
    singleton clusters and simply wait — a cluster below batch size is not an
    error, it is a theme still accumulating.
    """
    counts: dict[str, int] = {}
    for m in members:
        for k in {str(k).lower() for k in m.get("keys") or []}:
            counts[k] = counts.get(k, 0) + 1
    unclaimed = list(members)
    clusters: list[list[Mapping]] = []
    for key, _n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        got = [m for m in unclaimed if key in {str(k).lower() for k in m.get("keys") or []}]
        if len(got) >= 2:
            clusters.append(got)
            unclaimed = [m for m in unclaimed if m not in got]
    clusters.extend([m] for m in unclaimed)
    return clusters


# --------------------------------------------------------------------------- #
# Casting: facts -> day crystals                                              #
# --------------------------------------------------------------------------- #
def _fact_text(row: Mapping) -> str:
    what = row.get("what") or {}
    if isinstance(what, Mapping):
        action = str(what.get("action") or "").strip()
        obj = str(what.get("object") or "").strip()
    else:
        action, obj = "", str(what)
    who = row.get("who") or {}
    actor = str(who.get("actor") if isinstance(who, Mapping) else who or "").strip()
    why = str(row.get("why") or "").strip()
    core = " ".join(x for x in (actor, action + ":" if action else "", obj) if x)
    return f"{core} ({why})" if why else core


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cast_day_crystals(
    rows: Sequence[Mapping],
    store_path: str | Path,
    *,
    floor: float = 0.7,
    now: datetime | None = None,
    store_file_label: str = "canon",
    config: SmeltConfig | None = None,
) -> list[dict]:
    """Cast each worthy, uncast fact into a day crystal. Returns the newly cast.

    Worth is *birth* intensity clearing ``floor`` — the same "the edges of the
    day become crystals" rule as the reference deployment. Casting is
    idempotent with no markers: a fact already cited by a day crystal in the
    store is skipped (see :func:`cast_fact_ids`).
    """
    now = now or datetime.now(timezone.utc)
    crystals = load_crystals(store_path)
    already = cast_fact_ids(crystals)
    born: list[dict] = []
    for row in rows:
        rid = str(row.get("id") or "")
        if not rid or rid in already:
            continue
        try:
            inten = float(row.get("intensity") or 0.0)
        except (TypeError, ValueError):
            continue
        if inten < floor:
            continue
        text = _fact_text(row)
        when = str(row.get("valid_at") or row.get("ts") or now.isoformat())
        # The crystal is born OF that day: its clock anchors to the event time
        # (two-times discipline — decay runs from when it happened, not from
        # when the furnace got around to casting it).
        try:
            born_dt = datetime.fromisoformat(when.replace("Z", "+00:00"))
            if born_dt.tzinfo is None:
                born_dt = born_dt.replace(tzinfo=timezone.utc)
            born_ts = born_dt.astimezone(timezone.utc).isoformat()
        except (ValueError, TypeError):
            born_ts = now.isoformat()
        ptr = {"file": store_file_label, "t0": when, "t1": when, "sha": _sha(text)}
        result = smelt(
            text,
            [row],
            birth_affect={"v": float(row.get("valence") or 0.0), "a": 0.0},
            ts_utc=born_ts,
            mid=f"day-{rid}",
            source_ptrs=[ptr],
            src_ids=[rid],
            config=config,
        )
        crystal = result.get("crystal")
        if crystal is None:
            continue
        crystal = dict(crystal)
        crystal["tier"] = "day"
        crystal["keys"] = [str(k) for k in row.get("keys") or []]
        crystal["src_ids"] = [rid]
        _append(store_path, crystal)
        born.append(crystal)
    return born


# --------------------------------------------------------------------------- #
# Climbing: one pass over every rung, bottom-up                               #
# --------------------------------------------------------------------------- #
def ladder_pass(
    store_path: str | Path,
    *,
    dials: Sequence[TierDial] = DEFAULT_LADDER,
    summarize: Summarizer | None = None,
    now: datetime | None = None,
    config: SmeltConfig | None = None,
) -> dict:
    """Climb the ladder once: melt every cluster that has earned its rung.

    Bottom-up, so a week forged early in this pass can already feed a month
    later in the same pass; what a rung has melted never melts again (the
    reference line's "melted material never re-enters the furnace" holds by
    derivation: members cited above are skipped forever). Returns a report::

        {"melted": [crystal, ...], "waiting": {tier: n_members_still_short}}
    """
    now = now or datetime.now(timezone.utc)
    summarize = summarize or _join_summarizer
    report: dict = {"melted": [], "waiting": {}}
    for dial in dials:
        crystals = load_crystals(store_path)
        gone = absorbed_mids(crystals)
        members = [
            c
            for c in crystals
            if c.get("tier") == dial.name
            and str(c.get("mid")) not in gone
            and heat_now(c, now, dial.half_life_days) >= dial.min_heat
        ]
        short = 0
        for cluster in cluster_by_key(members):
            while len(cluster) >= dial.batch:
                batch, cluster = cluster[: dial.batch], cluster[dial.batch :]
                texts = [str(c.get("text") or "") for c in batch]
                fused_rows = [
                    {"intensity": heat_now(c, now, dial.half_life_days)} | dict(c) for c in batch
                ]
                ptrs = [
                    p
                    for c in batch
                    for p in (c.get("fingerprint") or {}).get("core", {}).get("source_ptrs", [])
                ] or [
                    {
                        "file": "ladder",
                        "t0": now.isoformat(),
                        "t1": now.isoformat(),
                        "sha": _sha("".join(texts)),
                    }
                ]
                mids = [str(c.get("mid")) for c in batch]
                result = smelt(
                    summarize(texts),
                    fused_rows,
                    birth_affect={"v": 0.0, "a": 0.0},
                    ts_utc=now.isoformat(),
                    mid=f"{dial.melts_to}-" + _sha("|".join(mids))[:12],
                    source_ptrs=ptrs,
                    src_ids=mids,
                    config=config,
                )
                crystal = result.get("crystal")
                if crystal is None:
                    break  # gate said no; members stay live for a better summary
                crystal = dict(crystal)
                crystal["tier"] = dial.melts_to
                crystal["heat"] = round(
                    min(1.0, float(crystal.get("heat") or 0) + dial.climb_bonus), 3
                )
                crystal["keys"] = sorted({str(k) for c in batch for k in c.get("keys") or []})
                crystal["src_ids"] = mids
                _append(store_path, crystal)
                report["melted"].append(crystal)
            short += len(cluster)
        if short:
            report["waiting"][dial.name] = short
    return report
