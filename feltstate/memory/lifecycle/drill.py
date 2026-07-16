"""feltstate.memory.lifecycle.drill — walk a memory's genealogy back to its roots.

A fused memory copies a flat union of every descendant's source pointer into its
own core (see :mod:`.fingerprint`), so *what* material it stands on is always in
hand. What the flat core cannot tell you is the *shape* of the descent — which
memory each pointer came through, and what the agent felt at each step down.
``drill`` recovers that shape: from any fingerprint it follows ``lineage`` (fused
children) and ``src`` (source memories) down to the leaves, carrying the birth
affect and timestamp at every node.

The store is the caller's, so drilling takes a ``resolve(mid) -> fingerprint``
lookup. A ``mid`` that resolves to ``None`` is a branch that has been pruned or
lost: the walk records it under ``lost`` (this is the *instinct memory* of
:mod:`.fingerprint` — the flat pointers in the ancestor's core still name the
original material, but the middle of the story is gone) and keeps going. The walk
is cycle-guarded (an already-seen ``mid`` becomes a ``revisited`` stub) and
depth-bounded.

The leaves' ``source_ptrs`` name a file and a time window in your transcript;
turn those into the actual lines with
:func:`feltstate.memory.context.get_turn_range_context`, which uses the pointer's
full inclusive ``t0``–``t1`` range. :func:`leaf_pointers` collects them for you.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ..context import get_turn_range_context
from .fingerprint import verify_fingerprint, verify_source_ptr

# Look up a memory's fingerprint by its ``mid`` (the caller owns the store).
Resolver = Callable[[str], Mapping | None]
TurnLoader = Callable[[str], list[dict]]
SourceTextLoader = Callable[[Mapping], str | None]


def drill(fp: Mapping, resolve: Resolver, *, max_depth: int = 64) -> dict:
    """Walk ``fp``'s genealogy to its leaves, returning a provenance tree.

    Each node is ``{mid, fp_id, verified, ts, birth_affect, source_ptrs,
    children, sources, lost}`` — ``children`` are drilled ``lineage`` (fused
    children), ``sources`` are drilled ``src`` (source memories), and ``lost``
    lists the mids on either edge that ``resolve`` could not find. A node reached
    a second time carries ``revisited=True`` and is not re-expanded; a node past
    ``max_depth`` carries ``truncated=True``.
    """
    return _walk(fp, resolve, set(), 0, max_depth)


def _walk(fp: Mapping, resolve: Resolver, seen: set[str], depth: int, max_depth: int) -> dict:
    core_raw = fp.get("core")
    core = core_raw if isinstance(core_raw, Mapping) else {}
    affect_raw = core.get("birth_affect")
    affect = dict(affect_raw) if isinstance(affect_raw, Mapping) else {}
    ptrs_raw = core.get("source_ptrs")
    ptrs = (
        [dict(pointer) for pointer in ptrs_raw if isinstance(pointer, Mapping)]
        if isinstance(ptrs_raw, list)
        else []
    )
    mid = str(fp.get("mid") or "")
    node: dict = {
        "mid": mid,
        "fp_id": str(fp.get("fp_id") or ""),
        "verified": verify_fingerprint(fp),
        "ts": str(core.get("ts") or ""),
        "birth_affect": affect,
        "source_ptrs": ptrs,
        "children": [],
        "sources": [],
        "lost": [],
    }
    if mid and mid in seen:
        node["revisited"] = True
        return node
    if depth >= max_depth:
        node["truncated"] = True
        return node
    if mid:
        seen.add(mid)
    for edge, out_key in (("lineage", "children"), ("src", "sources")):
        edge_raw = fp.get(edge)
        edge_values = edge_raw if isinstance(edge_raw, list) else []
        for raw_mid in edge_values:
            child_mid = str(raw_mid)
            try:
                child = resolve(child_mid)
            except Exception:
                child = None
            if not isinstance(child, Mapping):
                node["lost"].append(child_mid)
            else:
                node[out_key].append(_walk(child, resolve, seen, depth + 1, max_depth))
    return node


def leaf_pointers(tree: Mapping) -> list[dict]:
    """Return every distinct raw source pointer the tree still stands on.

    Resolved descendants provide their own leaf pointers. A fused node also
    carries copied pointers in its sealed core, so any pointer *not* represented
    by a resolvable descendant is retained too — this is what preserves a
    partially-pruned branch instead of silently losing its original evidence.
    De-duplication uses the complete pointer identity, not only ``sha``: the same
    words at two different times are two distinct pieces of evidence.
    """

    def key(ptr: Mapping) -> tuple[str, str, str, str]:
        return (
            str(ptr.get("file", "")),
            str(ptr.get("t0", "")),
            str(ptr.get("t1", "")),
            str(ptr.get("sha", "")),
        )

    def collect(node: Mapping) -> dict[tuple[str, str, str, str], dict]:
        found: dict[tuple[str, str, str, str], dict] = {}
        kids = list(node.get("children") or []) + list(node.get("sources") or [])
        for child in kids:
            if isinstance(child, Mapping):
                found.update(collect(child))
        # If the node is a true leaf, all of its pointers are evidence. If it has
        # descendants, only pointers missing from those descendants represent a
        # direct or lost/pruned branch; update() naturally keeps both cases.
        for raw in node.get("source_ptrs") or []:
            if isinstance(raw, Mapping):
                ptr = dict(raw)
                found.setdefault(key(ptr), ptr)
        return found

    return list(collect(tree).values())


def trace_contexts(
    tree: Mapping,
    load: TurnLoader,
    *,
    before: int = 2,
    after: int = 2,
    load_source_text: SourceTextLoader | None = None,
) -> list[dict]:
    """Resolve leaf pointers into transcript ranges and optional hash checks.

    ``load(file)`` is supplied by the caller because feltstate does not own the
    transcript store. The pointer's inclusive ``t0``–``t1`` range is resolved,
    rather than treating only ``t0`` as a point anchor. Each item is
    ``{"pointer": ..., "context": ..., "source_verified": bool | None}``.

    Exact verification is optional because applications serialise transcript
    windows differently. When ``load_source_text(pointer)`` is supplied, its
    returned text is checked with :func:`verify_source_ptr`; loader failures are
    recorded per branch and never abort the rest of the trace.
    """
    out: list[dict] = []
    for pointer in leaf_pointers(tree):
        file = str(pointer.get("file", ""))
        try:
            turns = load(file)
            if not isinstance(turns, list):
                context = {"ok": False, "reason": "loader did not return a list"}
            else:
                context = get_turn_range_context(
                    turns,
                    str(pointer.get("t0", "")),
                    str(pointer.get("t1", "")),
                    before=before,
                    after=after,
                )
        except Exception as exc:  # caller-owned storage adapter; keep other branches usable
            context = {"ok": False, "reason": f"loader failed: {type(exc).__name__}: {exc}"}

        verified: bool | None = None
        verification_error: str | None = None
        if load_source_text is not None:
            try:
                exact_text = load_source_text(pointer)
                if exact_text is None:
                    verification_error = "source-text loader returned None"
                elif not isinstance(exact_text, str):
                    verification_error = "source-text loader did not return a string"
                else:
                    verified = verify_source_ptr(pointer, exact_text)
            except Exception as exc:
                verification_error = (
                    f"source-text loader failed: {type(exc).__name__}: {exc}"
                )

        item: dict[str, Any] = {
            "pointer": pointer,
            "context": context,
            "source_verified": verified,
        }
        if verification_error is not None:
            item["verification_error"] = verification_error
        out.append(item)
    return out


def trace_memory(
    fp: Mapping,
    resolve: Resolver,
    load_turns: TurnLoader,
    *,
    before: int = 2,
    after: int = 2,
    load_source_text: SourceTextLoader | None = None,
    max_depth: int = 64,
) -> dict:
    """One-call provenance report for a memory fingerprint.

    Returns the drilled genealogy, distinct leaf pointers, resolved transcript
    ranges, affect-at-birth trail, and summary counts. The application still owns
    both stores; this function simply joins the public lifecycle primitives into
    one inspectable result.
    """
    tree = drill(fp, resolve, max_depth=max_depth)
    pointers = leaf_pointers(tree)
    contexts = trace_contexts(
        tree,
        load_turns,
        before=before,
        after=after,
        load_source_text=load_source_text,
    )
    trail = affect_trail(tree)
    return {
        "tree": tree,
        "leaf_pointers": pointers,
        "contexts": contexts,
        "affect_trail": trail,
        "n_leaf_pointers": len(pointers),
        "n_contexts_ok": sum(1 for item in contexts if item["context"].get("ok")),
        "n_sources_verified": sum(item.get("source_verified") is True for item in contexts),
        "n_lost_branches": _count_lost(tree),
    }


def _count_lost(tree: Mapping) -> int:
    total = len(tree.get("lost") or [])
    for child in list(tree.get("children") or []) + list(tree.get("sources") or []):
        if isinstance(child, Mapping):
            total += _count_lost(child)
    return total


def affect_trail(tree: Mapping) -> list[dict]:
    """A depth-first list of ``{mid, ts, birth_affect}`` at every node — the mood
    at each step of the descent, root first."""
    trail: list[dict] = []

    def visit(node: Mapping) -> None:
        trail.append(
            {
                "mid": node.get("mid", ""),
                "ts": node.get("ts", ""),
                "birth_affect": dict(node.get("birth_affect") or {}),
            }
        )
        for k in list(node.get("children") or []) + list(node.get("sources") or []):
            visit(k)

    visit(tree)
    return trail
