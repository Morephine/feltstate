"""feltstate.memory.lifecycle.reaper — when a memory dies, it actually dies.

Most agent memory systems "forget" by setting an invalidation flag and hiding
the row. The store never shrinks, the past never leaves, and — for a companion
that people confide in — anything anyone ever said is still on disk. This module
takes the other contract seriously: a memory judged dead by :mod:`.gc` is
physically removed from the live store **and from every snapshot it is given**,
in an order that survives a power cut at any step.

Every cascade carries a **transaction id** (``txid``). The pending ledger, the
tombstone, and replay all key off it, so a crash-and-replay produces exactly one
tombstone and one deletion, never duplicates.

The cascade:

1. **Write + fsync the pending ledger** (the plan, keyed by ``txid``). Its
   presence means "a deletion is in flight"; a fresh boot replays it. The file
   and its directory are fsynced, so the marker is durable before any row moves.
2. **Drop the tombstone** — a ``legal_death`` event appended (and fsynced) to
   the audit ledger *before* anything disappears, carrying the memory ids
   (``mid``) and store row keys so the tamper watchdog (see :mod:`.chain`) can
   tell a lawful death from an evaporation. Idempotent by ``txid``: replay does
   not append a second tombstone.
3. **Delete from the live stores** — keep only rows whose ``mid`` is not on the
   plan; prune dead heritage branches from the survivors. Atomic + fsynced.
4. **Purge the snapshots** — the same rows removed from every backup given. No
   regret medicine: disaster copies survive crashes, they do not resurrect the
   forgotten.
5. **Clear the pending ledger** — only after everything above.

Honest limit: **source-material rows are marked, not physically purged** — the
plan's ``dead_sources`` are recorded in the tombstone as eligible-for-deletion,
but the archive adapter that rewrites source files is future work. This module
executes a plan; who deserves to die is decided in :mod:`.gc`, never here.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path

__all__ = [
    "execute",
    "replay_if_pending",
    "ReaperError",
]


class ReaperError(RuntimeError):
    pass


def _read_jsonl(p: Path) -> list[dict]:
    """Read a JSONL file, skipping any line that is not a complete record.

    The tombstone below is an append, so a crash mid-write leaves a partial
    final line. A bare ``json.loads`` over the file then raised
    ``JSONDecodeError`` on every subsequent ``execute()`` *and*
    ``replay_if_pending()`` — the pending ledger could never be cleared and the
    deletion could never finish. A crash-recovery module has to survive the
    crash it exists for; ``canon._load_jsonl`` already quarantines bad lines
    rather than dying on them.

    A torn line is dropped: it is by definition an event that was never
    completely recorded, so no live decision may depend on it.
    """
    if not p.exists():
        return []
    rows: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _fsync_path(p: Path) -> None:
    """Best-effort durability: fsync the file, then its parent directory so the
    rename/creation itself is on stable storage."""
    try:
        fd = os.open(str(p), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        dfd = os.open(str(p.parent), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def _atomic_write_jsonl(p: Path, rows: Sequence[dict]) -> None:
    tmp = p.with_suffix(p.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )
    tmp.replace(p)
    _fsync_path(p)


def _key(row: dict):
    return (row.get("fp") or {}).get("mid")


def _tombstone_exists(ledger_path: Path, txid: str) -> bool:
    for j in _read_jsonl(ledger_path):
        if j.get("event") == "legal_death" and j.get("txid") == txid:
            return True
    return False


def execute(
    plan: dict,
    *,
    stores: Sequence[Path],
    ledger_path: Path,
    pending_path: Path,
    txid: str,
    snapshot_paths: Sequence[Path] | None = None,
    now_iso: str = "",
) -> dict:
    """Run the cascade for one death plan under transaction id ``txid``.
    ``stores`` are the live jsonl files; ``snapshot_paths`` every backup that
    must forget too. Idempotent by ``txid``: crash anywhere, call
    :func:`replay_if_pending` on boot, end state is identical."""
    dead = set(plan.get("dead_ids", []))
    dead_sources = plan.get("dead_sources", [])
    prune = plan.get("prune", {})
    snaps = [Path(s) for s in (snapshot_paths or [])]

    # 1) pending ledger, fsynced — the replay anchor.
    _atomic_write_jsonl(
        pending_path,
        [
            {
                "txid": txid,
                "dead_ids": sorted(dead),
                "dead_sources": [list(s) for s in dead_sources],
                "prune": prune,
                "snaps": [str(s) for s in snaps],
                "ts": now_iso,
            }
        ],
    )

    # 2) tombstone first, idempotent by txid — lawful death before disappearance.
    if (dead or dead_sources) and not _tombstone_exists(ledger_path, txid):
        row_keys = []
        for path in stores:
            for r in _read_jsonl(path):
                if _key(r) in dead and r.get("cid"):
                    row_keys.append(r["cid"])
        # One complete line, written and flushed in a single call, so a crash
        # can only leave the ledger with the tombstone fully present or fully
        # absent — never half of it. (_read_jsonl also tolerates a torn line
        # from an older file.)
        with ledger_path.open("a", encoding="utf-8") as lf:
            lf.write(
                json.dumps(
                    {
                        "event": "legal_death",
                        "txid": txid,
                        "ids": sorted(dead),
                        "cids": sorted(set(row_keys)),
                        "sources": [list(s) for s in dead_sources],
                        "ts": now_iso,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        _fsync_path(ledger_path)

    # 3) delete from the live stores; prune heritage on the survivors.
    for path in stores:
        rows = _read_jsonl(path)
        kept = [r for r in rows if _key(r) not in dead]
        for r in kept:
            fp = r.get("fp")
            if fp and _key(r) in prune:
                fp["lineage"] = [x for x in fp.get("lineage", []) if x not in set(prune[_key(r)])]
        if len(kept) != len(rows) or any(_key(r) in prune for r in kept):
            _atomic_write_jsonl(path, kept)

    # 4) purge every snapshot — forgetting includes the backups.
    for snap in snaps:
        if snap.exists():
            rows = _read_jsonl(snap)
            kept = [r for r in rows if _key(r) not in dead]
            if len(kept) != len(rows):
                _atomic_write_jsonl(snap, kept)

    # 5) clear pending — the cascade is complete only now.
    if pending_path.exists():
        pending_path.unlink()
        _fsync_path(pending_path.parent)

    return {
        "txid": txid,
        "deleted": sorted(dead),
        "sources_marked": [list(s) for s in dead_sources],  # physical purge = future work
        "pruned": dict(prune),
    }


def replay_if_pending(
    *, stores: Sequence[Path], ledger_path: Path, pending_path: Path
) -> dict | None:
    """Boot-time recovery: a pending ledger means a cascade died mid-way — rerun
    it verbatim under its own ``txid`` (idempotent). Fails closed on a malformed
    or empty pending record: it is left in place and raised, never silently
    dropped, because it might represent a half-finished deletion."""
    if not pending_path.exists():
        return None
    rows = _read_jsonl(pending_path)
    if not rows or not isinstance(rows[0], dict) or not rows[0].get("txid"):
        raise ReaperError(
            f"malformed pending ledger at {pending_path}; "
            "refusing to discard a possibly half-finished deletion"
        )
    plan = rows[0]
    return execute(
        plan,
        stores=stores,
        ledger_path=ledger_path,
        pending_path=pending_path,
        txid=plan["txid"],
        snapshot_paths=[Path(s) for s in plan.get("snaps", [])],
        now_iso=plan.get("ts", ""),
    )
