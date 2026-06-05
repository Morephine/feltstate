"""feltstate.companion.topics — the pending-topics queue seam.

A companion can leave itself a note to raise something the next time it speaks
unprompted ("ask how the deploy went"). This is the store for those notes. The
scheduler depends only on the :class:`PendingTopicsStore` interface;
:class:`JsonlTopicsStore` is a zero-dependency reference implementation.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path


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
    rewrites the file. Append is plain text-append; reads tolerate partial/bad
    lines. Zero-dependency.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"text": text, "consumed": False}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def read_oldest_unconsumed(self) -> str | None:
        for rec in self._read():
            if not rec.get("consumed"):
                text = rec.get("text")
                return str(text) if text is not None else None
        return None

    def mark_consumed(self, text: str) -> None:
        recs = self._read()
        for rec in recs:
            if not rec.get("consumed") and rec.get("text") == text:
                rec["consumed"] = True
                self.path.write_text(
                    "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs),
                    encoding="utf-8",
                )
                return
