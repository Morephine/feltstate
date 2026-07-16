"""feltstate.memory.lifecycle.fingerprint — birth records for checkable
provenance.

This optional module lets a stored memory carry a **birth fingerprint** at the
moment it is written: a pointer to
the source material it was distilled from, the affect the agent was feeling at
that moment, and a UTC timestamp — hashed together into a content id, so any
later edit to the sealed record is *detectable*.

Two ids, on purpose (this distinction is load-bearing — see below):

* ``mid`` — the **memory's unique instance id**, supplied by the store (its row
  key). It identifies *this* memory and nothing else. Every edge, plan and
  deletion keys off ``mid``.
* ``fp_id`` — the **content hash** of the immutable core. Two memories born from
  the same source, affect and instant legitimately share an ``fp_id``; it is an
  integrity checksum, never an identity. Keying identity off content is how a
  collector ends up deleting every row that happens to hash alike, so we never
  do it.

One honest caveat: an unkeyed content hash is not authentication or encryption
— whoever can rewrite the whole row can also recompute the hash.
The trust anchor is external: :mod:`.chain` bites each ``fp_id`` into a
hash-linked ledger, so a recomputed hash shows up as a mutation on the next
patrol. Fingerprint and chain are designed as a pair.

The record has two layers with opposite contracts:

* the **immutable core** (``source_ptrs`` + ``birth_affect`` + ``ts``) is hashed
  into ``fp_id``. Change one byte of it and :func:`verify_fingerprint` fails.
* the **living genealogy** (``src`` and ``lineage``, kept *outside* the core,
  and holding ``mid`` values) may change over the memory's life. Pruning a dead
  branch of ancestry is a life event, not forgery, so it never breaks the seal.

Fusion copies the children's source pointers *into* the new core (a real union,
not references) and records the children's ``mid`` values in ``lineage``. When a
child dies and its branch is pruned, the fused memory keeps the copies in its
own body; it loses the *middle* of its story — which memory that part came
through — while the copied raw pointers still name the original material until
the source archive expires. We call the end state an **instinct memory**.

All constructors validate their inputs and fail closed: timestamps must be
timezone-aware UTC, affect values must be finite numbers, and no NaN/Infinity
can enter the sealed core.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

_SHA_RE = re.compile(r"(?:[0-9a-f]{16}|[0-9a-f]{64})\Z")

__all__ = [
    "make_source_ptr",
    "verify_source_ptr",
    "core_hash",
    "make_fingerprint",
    "verify_fingerprint",
    "fuse",
    "prune_lineage",
    "FingerprintError",
]


class FingerprintError(ValueError):
    """Raised by constructors when an input violates the fingerprint contract.
    (Constructors fail loudly; :func:`verify_fingerprint` fails closed instead,
    returning ``False`` rather than raising, because it runs on untrusted data.)"""


def _text_hash(text: str) -> str:
    if not isinstance(text, str):
        raise FingerprintError("source text must be a str")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical(obj: Any) -> str:
    # allow_nan=False rejects NaN/Infinity rather than emitting non-standard JSON
    return json.dumps(
        obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def _check_utc(ts: str) -> str:
    if not isinstance(ts, str) or not ts:
        raise FingerprintError("ts must be a non-empty ISO-8601 string")
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError as e:
        raise FingerprintError(f"ts is not ISO-8601: {ts!r}") from e
    if dt.tzinfo is None or dt.utcoffset() != timezone.utc.utcoffset(None):
        raise FingerprintError(f"ts must be timezone-aware UTC, got {ts!r}")
    return ts


def _check_affect(affect: Mapping) -> dict:
    if not isinstance(affect, Mapping):
        raise FingerprintError("birth_affect must be a mapping")
    out = {}
    for k, v in affect.items():
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
            raise FingerprintError(f"birth_affect[{k!r}] must be a finite number, got {v!r}")
        out[str(k)] = float(v)
    return out


def _check_ptrs(source_ptrs: Sequence[Mapping]) -> list[dict]:
    ptrs = list(source_ptrs)
    if not ptrs:
        raise FingerprintError(
            "a fingerprint needs at least one source pointer (empty provenance is not a memory)"
        )
    out = []
    for p in ptrs:
        if not isinstance(p, Mapping):
            raise FingerprintError("each source pointer must be a mapping")
        file = str(p.get("file", "")).strip()
        t0 = str(p.get("t0", "")).strip()
        t1 = str(p.get("t1", "")).strip()
        if not file:
            raise FingerprintError("source pointer needs a non-empty 'file'")
        if not t0 or not t1:
            raise FingerprintError("source pointer needs non-empty 't0' and 't1'")
        sha = p.get("sha")
        if not isinstance(sha, str) or not _SHA_RE.fullmatch(sha):
            raise FingerprintError(
                "source pointer sha must be 64 lowercase hex characters "
                "(or the legacy 16-character prefix)"
            )
        out.append(
            {
                "file": file,
                "t0": t0,
                "t1": t1,
                "sha": sha,
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Building blocks                                                             #
# --------------------------------------------------------------------------- #
def make_source_ptr(file: str, t0: str, t1: str, text: str) -> dict:
    """One pointer at source material: which file, which time window, and a
    hash of the exact text the memory was distilled from.

    ``text`` must be byte-identical to what the source store keeps for that
    window — hash the text you *write*, not a differently-truncated copy of it,
    or the pointer can never be re-verified against the archive later.
    """
    file_s, t0_s, t1_s = str(file).strip(), str(t0).strip(), str(t1).strip()
    if not file_s:
        raise FingerprintError("source pointer file must be non-empty")
    if not t0_s or not t1_s:
        raise FingerprintError("source pointer t0/t1 must be non-empty")
    return {"file": file_s, "t0": t0_s, "t1": t1_s, "sha": _text_hash(text)}


def verify_source_ptr(ptr: Mapping, text: str) -> bool:
    """Return whether ``text`` matches the hash sealed in ``ptr``.

    This verifies exact UTF-8 text equality only. It does not locate or decrypt
    source material; the caller still owns the transcript/archive and chooses
    how a pointer's time window is serialised back into text.
    """
    try:
        checked = _check_ptrs([ptr])[0]
        actual = _text_hash(text)
        expected = checked["sha"]
        # Early alpha builds used the first 16 hex characters. New pointers use
        # the full SHA-256 digest, while verification remains backward-compatible
        # with those already-written short pointers.
        if len(expected) == 16:
            actual = actual[:16]
        return hmac.compare_digest(expected, actual)
    except (FingerprintError, TypeError):
        return False


def core_hash(source_ptrs: Sequence[Mapping], birth_affect: Mapping, ts: str) -> str:
    """Full sha256 of the immutable core — the integrity checksum ``fp_id``.
    Full length (not truncated); it is a checksum backed by the chain, never a
    key, so collision here is a verification concern, not a deletion one."""
    core = {
        "source_ptrs": [dict(p) for p in source_ptrs],
        "birth_affect": dict(birth_affect),
        "ts": ts,
    }
    return hashlib.sha256(_canonical(core).encode("utf-8")).hexdigest()


def make_fingerprint(
    source_ptrs: Sequence[Mapping],
    birth_affect: Mapping,
    ts_utc: str,
    *,
    mid: str,
    src: Sequence[str] | None = None,
    lineage: Sequence[str] | None = None,
) -> dict:
    """Mint a fingerprint at a memory's birth.

    ``mid`` — the memory's unique instance id (its store row key). REQUIRED,
    non-empty; it is what identity, edges and deletion key off. ``birth_affect``
    is the affect snapshot *at the moment of the event* (finite numbers,
    recorded by the program). ``src`` / ``lineage`` hold the ``mid`` values of
    source memories and fused children respectively, and live outside the core.
    """
    if not isinstance(mid, str) or not mid:
        raise FingerprintError("mid (unique memory id) is required and non-empty")
    ptrs = _check_ptrs(source_ptrs)
    affect = _check_affect(birth_affect)
    ts = _check_utc(ts_utc)
    core = {"source_ptrs": ptrs, "birth_affect": affect, "ts": ts}
    fp_id = core_hash(ptrs, affect, ts)
    return {
        "mid": mid,
        "fp_id": fp_id,
        "core": core,
        "src": [str(x) for x in (src or [])],
        "lineage": [str(x) for x in (lineage or [])],
    }


def verify_fingerprint(fp: Any) -> bool:
    """Recompute the core hash and compare with ``fp_id``. Fails **closed**:
    any malformed input returns ``False`` rather than raising, because this runs
    on untrusted, possibly-tampered data. Genealogy edits never affect it."""
    try:
        if not isinstance(fp, Mapping):
            return False
        if not isinstance(fp.get("mid"), str) or not fp["mid"]:
            return False
        if not isinstance(fp.get("fp_id"), str) or not fp["fp_id"]:
            return False
        for edge in ("src", "lineage"):
            values = fp.get(edge, [])
            if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
                return False
            if any(not isinstance(value, str) for value in values):
                return False
        c = fp["core"]
        if not isinstance(c, Mapping) or set(c) != {"source_ptrs", "birth_affect", "ts"}:
            return False
        raw_ptrs = c["source_ptrs"]
        if not isinstance(raw_ptrs, Sequence) or isinstance(raw_ptrs, (str, bytes)):
            return False
        # Constructors emit a closed pointer schema. Extra/missing fields in the
        # immutable core are mutation, not harmless metadata.
        if any(
            not isinstance(p, Mapping) or set(p) != {"file", "t0", "t1", "sha"}
            for p in raw_ptrs
        ):
            return False
        ptrs = _check_ptrs(raw_ptrs)
        affect = _check_affect(c["birth_affect"])
        ts = _check_utc(c["ts"])
        expected = core_hash(ptrs, affect, ts)
        return hmac.compare_digest(expected, fp["fp_id"])
    except (FingerprintError, KeyError, TypeError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# Life events                                                                 #
# --------------------------------------------------------------------------- #
def fuse(children_fps: Sequence[Mapping], birth_affect: Mapping, ts_utc: str, *, mid: str) -> dict:
    """Fusion: several memories condense into one (with its own ``mid``).

    The children's source pointers are **copied into the new core** — a real
    union, not references. The children's ``mid`` values go into ``lineage``
    (prunable heritage). If every source of one branch later dies, prune the
    branch: the fused memory stands on the copies in its own body, now unable to
    name that part of its origin. That is the instinct memory, by design.
    """
    merged: dict[tuple[str, str, str, str], dict] = {}
    lineage: list[str] = []
    for cfp in children_fps:
        if not verify_fingerprint(cfp):
            raise FingerprintError("fusion child has an invalid or unverifiable fingerprint")
        child_mid = str(cfp.get("mid") or "")
        if child_mid and child_mid not in lineage:
            lineage.append(child_mid)
        for raw in cfp.get("core", {}).get("source_ptrs", []):
            p = dict(raw)
            key = (str(p["file"]), str(p["t0"]), str(p["t1"]), str(p["sha"]))
            merged.setdefault(key, p)
    if not merged:
        raise FingerprintError("fusion needs children carrying source pointers")
    return make_fingerprint(
        list(merged.values()),
        birth_affect,
        ts_utc,
        mid=mid,
        lineage=lineage,
    )


def prune_lineage(fp: dict, dead_mids: set) -> dict:
    """Cut dead branches out of a living fused memory's heritage. Mutates and
    returns ``fp``. Only ``lineage`` changes — ``fp_id`` stays, the seal stays,
    :func:`verify_fingerprint` still passes. Forgetting an ancestor is a life
    event; it is not, and must never look like, tampering."""
    fp["lineage"] = [x for x in fp.get("lineage", []) if x not in dead_mids]
    return fp
