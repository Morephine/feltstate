"""feltstate.companion.topics — the pending-topics queue seam.

A companion can leave itself a note to raise something the next time it speaks
unprompted ("ask how the deploy went"). This is the store for those notes. The
scheduler depends only on the :class:`PendingTopicsStore` interface;
:class:`JsonlTopicsStore` is a zero-dependency reference implementation.
"""

from __future__ import annotations

import json
import threading
from abc import ABC, abstractmethod
from pathlib import Path

# Cross-platform advisory locking (2026-07-18 fix). The previous top-level
# ``import fcntl`` made importing feltstate.companion crash on Windows — the
# very platform the concurrency fix was meant to protect. Mirror
# memory/canon.py: use flock where available, else fall back to a per-process
# threading lock (single-process safety, the common case).
try:  # pragma: no cover - platform dependent
    import fcntl as _fcntl
except ImportError:  # Windows
    _fcntl = None

_FALLBACK_LOCK = threading.Lock()


def _flock_ex(fh) -> None:
    if _fcntl is not None:
        _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX)
    else:
        _FALLBACK_LOCK.acquire()


def _flock_un(fh) -> None:
    if _fcntl is not None:
        _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
    else:
        _FALLBACK_LOCK.release()


class PendingTopicsStore(ABC):
    """Append-only queue of things the companion means to bring up later."""

    @abstractmethod
    def append(self, text: str) -> None:
        """Add a topic to raise later."""
        ...

    @abstractmethod
    def read_oldest_unconsumed(self) -> str | None:
        """Return the oldest not-yet-raised topic, or ``None`` if none."""
        ...

    @abstractmethod
    def mark_consumed(self, text: str) -> None:
        """Mark the oldest matching unconsumed topic as raised."""
        ...


class JsonlTopicsStore(PendingTopicsStore):
    """JSONL reference impl — one ``{"text", "consumed"}`` record per line.

    Oldest unconsumed wins; ``mark_consumed`` flips the first matching record and
    rewrites the file atomically. Reads tolerate partial/bad lines.
    Every mutation holds one shared advisory lock (v0.2.1) — concurrency and
    atomicity parity with the other write paths (see ``memory.canon``).
    Zero-dependency.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _locked(self):
        """One shared lock file for *every* mutation. Appends and the
        consume-rewrite previously used no lock, so an append racing the
        rewrite could vanish with the replaced inode."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return (self.path.with_name(self.path.name + ".lock")).open("w")

    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        out: list[dict] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def append(self, text: str) -> None:
        rec = {"text": text, "consumed": False}
        with self._locked() as lk:
            _flock_ex(lk)
            try:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            finally:
                _flock_un(lk)

    def read_oldest_unconsumed(self) -> str | None:
        for rec in self._read():
            if not rec.get("consumed"):
                text = rec.get("text")
                return str(text) if text is not None else None
        return None

    def mark_consumed(self, text: str) -> None:
        # Read-modify-rewrite under the shared lock, landing via tmp+replace —
        # a crash mid-rewrite must never eat the whole queue.
        with self._locked() as lk:
            _flock_ex(lk)
            try:
                recs = self._read()
                for rec in recs:
                    if not rec.get("consumed") and rec.get("text") == text:
                        rec["consumed"] = True
                        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
                        tmp.write_text(
                            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs),
                            encoding="utf-8",
                        )
                        tmp.replace(self.path)
                        return
            finally:
                _flock_un(lk)
