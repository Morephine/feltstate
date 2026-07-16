"""feltstate.memory.lifecycle.gc — decide who dies, with authority flowing
downhill and a hard mercy rule for the unfingerprinted.

Forgetting is not one deletion — it is a cascade with politics. A distilled
life-lesson outranks the small facts it grew from: while the lesson lives, its
facts may not be collected out from under it, however faded they look. When the
lesson itself dies, the facts fall back on their own clocks. Source material
(the raw archive a fingerprint points at) dies last of all — only when nothing
alive references it anymore.

This module is the **judge, not the executioner**: :func:`resolve_deaths` is a
pure computation over a list of memories and returns a death plan keyed by
``mid`` (the unique memory instance id). Nothing here touches a file (see
:mod:`.reaper` for the actual cascade).

Rules, in order of precedence:

1. **No valid fingerprint, no death.** A memory with no fingerprint — or one
   whose fingerprint fails :func:`~.fingerprint.verify_fingerprint`, or is a
   back-filled legacy record — is exempt forever. A collector must never kill
   what it cannot *verifiably* trace; syntactic presence of an id is not
   provenance.
2. **Retention comes from the caller.** ``intensity_fn`` gives each memory its
   intensity today; a memory at or below ``death_line`` is *eligible*, not
   doomed.
3. **Protection edges, resolved to a fixed point.** A memory of a protector
   kind that is *retained* (alive on its own clock, or shielded by another
   retained protector) shields every ``mid`` in its ``src``. Authority is
   transitive: a living grandparent keeps a faded parent, which in turn keeps
   the child.
4. **Heritage is not life-support.** Fusion ``lineage`` edges keep nobody
   alive; a dead lineage id is pruned from the living fused memory.
5. **Source material is reference-counted** by pointer identity (file + window
   + full-length hash), and anything referenced by an exempt memory is immortal
   by rule 1.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence

from .fingerprint import verify_fingerprint

__all__ = [
    "DEFAULT_DEATH_LINE",
    "is_collectable",
    "resolve_deaths",
    "GCError",
]

DEFAULT_DEATH_LINE = 0.05


class GCError(ValueError):
    """Raised when the store violates a GC precondition (e.g. duplicate mid)."""


def _mid(mem: Mapping):
    return (mem.get("fp") or {}).get("mid")


def is_collectable(mem: Mapping) -> bool:
    """True only if this memory may ever be collected: it has a fingerprint,
    the fingerprint verifies, and it is not a back-filled legacy record.
    Everything else is exempt (rule 1, fail-safe)."""
    fp = mem.get("fp")
    if not fp or fp.get("backfill") or not fp.get("mid"):
        return False
    return verify_fingerprint(fp)


def _source_key(ptr: Mapping):
    """Pointer identity = file + window + full hash. Never ``None``: a pointer
    without a usable identity is dropped from the reference graph entirely."""
    sha = ptr.get("sha")
    if not isinstance(sha, str) or not sha:
        return None
    return (str(ptr.get("file", "")), str(ptr.get("t0", "")), str(ptr.get("t1", "")), sha)


def _ptrs(mem: Mapping):
    fp = mem.get("fp") or {}
    return fp.get("core", {}).get("source_ptrs", [])


def resolve_deaths(
    mems: Sequence[Mapping],
    intensity_fn: Callable[[Mapping], float],
    *,
    death_line: float = DEFAULT_DEATH_LINE,
    protector_kinds: Iterable[str] = ("distilled",),
) -> dict:
    """Judge the whole store; return a death plan keyed by ``mid``. Pure.

    ``intensity_fn(mem)`` returns the memory's intensity today. ``death_line``
    is the eligibility threshold (defaults to :data:`DEFAULT_DEATH_LINE`; pass
    your :class:`~.clocks.ClockConfig`'s ``death_line`` to keep them in step).

    Returns ``{"dead_ids", "prune", "dead_sources", "kept_new",
    "skipped_legacy"}`` where every id is a ``mid``. Raises :class:`GCError` if
    two collectable memories share a ``mid`` (a store integrity violation that
    must be fixed before anything is deleted)."""
    protectors = set(protector_kinds)

    part: dict = {}
    for m in mems:
        if not is_collectable(m):
            continue
        k = _mid(m)
        if k in part:
            raise GCError(
                f"duplicate memory id {k!r}: keys must be unique before a death plan can be trusted"
            )
        part[k] = m

    legacy_sources = {
        sk for m in mems if not is_collectable(m) for sk in (_source_key(p) for p in _ptrs(m)) if sk
    }

    self_alive = {k: intensity_fn(m) > death_line for k, m in part.items()}

    # Rule 3 — resolve protection to a fixed point so authority is transitive.
    retained = dict(self_alive)
    changed = True
    while changed:
        changed = False
        shield: set = set()
        for k, m in part.items():
            if m.get("kind") in protectors and retained[k]:
                shield.update((m.get("fp") or {}).get("src", []))
        for k in part:
            if not retained[k] and k in shield:
                retained[k] = True
                changed = True

    dead = {k for k in part if not retained[k]}

    # Rule 4 — prune dead heritage branches from retained fused memories.
    prune: dict = {}
    for k, m in part.items():
        if k in dead:
            continue
        cut = [x for x in (m.get("fp") or {}).get("lineage", []) if x in dead]
        if cut:
            prune[k] = cut

    # Rule 5 — reference-count source material by pointer identity.
    source_refs: dict = {}
    for m in mems:
        mk = _mid(m) if is_collectable(m) else None
        for p in _ptrs(m):
            sk = _source_key(p)
            if sk is not None:
                source_refs.setdefault(sk, set()).add(mk)
    dead_sources = []
    for sk, refs in source_refs.items():
        if sk in legacy_sources:
            continue
        live = [r for r in refs if r in part and r not in dead]
        legacy_ref = [r for r in refs if r is None or r not in part]
        if not live and not legacy_ref:
            dead_sources.append(sk)

    return {
        "dead_ids": sorted(dead),
        "prune": prune,
        "dead_sources": sorted(dead_sources),
        "kept_new": len([k for k in part if k not in dead]),
        "skipped_legacy": len(mems) - len(part),
    }
