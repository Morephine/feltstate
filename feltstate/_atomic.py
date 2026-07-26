"""feltstate._atomic — write a file so a reader never sees it half-written.

Every state file here is rewritten whole: write a temporary sibling, then
``os.replace`` it over the target, which is atomic on POSIX and Windows. Two
details matter and were missing at several call sites.

**The temporary name must be unique.** A fixed ``<name>.tmp`` means two writers
of the same path race on one scratch file: whichever calls ``replace`` first
unlinks it, and the other's ``replace`` raises ``FileNotFoundError``. Measured
with six threads ticking one ``Engine``: five died that way. Uniqueness makes
the loser simply lose — last write wins, no crash.

**The rewrite needs a lock.** Uniqueness alone still lets two writers interleave
read-modify-write and lose one of the updates. An in-process lock per resolved
path serialises them. It is not cross-process — ``memory.canon`` layers ``flock``
on top for that — but it covers the case these files actually see: one process,
several threads (a scheduler tick alongside a foreground turn).
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = _LOCKS[key] = threading.RLock()
    return lock


@contextmanager
def path_lock(path: Path) -> Iterator[None]:
    """Serialise writers of ``path`` within this process."""
    with _lock_for(path):
        yield


def atomic_write_text(path: str | Path, text: str, *, encoding: str = "utf-8") -> None:
    """Replace ``path`` with ``text``, atomically and without racing a sibling."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with path_lock(p):
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident():x}.tmp")
        try:
            tmp.write_text(text, encoding=encoding)
            os.replace(tmp, p)
        finally:
            if tmp.exists():  # pragma: no cover - only on a failed write
                tmp.unlink(missing_ok=True)
