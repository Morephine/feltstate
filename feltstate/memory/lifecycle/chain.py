"""feltstate.memory.lifecycle.chain — a tamper-evident ledger that knows the
difference between a death and a disappearance.

A memory store that can be silently edited is not evidence of a life — it is a
scratchpad. This watchdog keeps an append-only, hash-linked ledger over any
jsonl store: each patrol snapshots ``{row_key: bite(row)}``, diffs it against
the previous link, and appends a new link whose hash chains over the previous
one. **The hash covers the previous link, the diff payload, and the full state
snapshot**, so editing any of them — including the stored state of the latest
link — breaks every later link's verification.

What each patrol distinguishes:

* **added** rows — legitimate births, recorded, never alarmed;
* **mutated** rows — the sealed text or the fingerprint id changed: ALARM.
  (Only immutable things are bitten in. Metadata that is *supposed* to evolve —
  recall counts, decay state, pruned lineage — stays out, so living never looks
  like tampering.)
* **missing** rows — checked against ``legal_death`` tombstones the reaper drops
  (see :mod:`.reaper`). A tombstoned disappearance is a lawful death: logged
  *into the chained payload* (so the fact of a lawful death is itself sealed in
  the chain, not merely asserted by a deletable event line). An untombstoned
  disappearance is an evaporation: ALARM. Note the fail-safe direction — delete
  a real tombstone and the next patrol alarms; it cannot go silent.

Retention: links and tombstones age out after ``keep_days``. Pruning does not
orphan the chain — the first surviving link is re-anchored as an **epoch**
whose ``prev`` commits to the hash of the last discarded link, so
:meth:`verify_full` has an explicit, self-describing starting point instead of
a link whose predecessor is gone. There is only ever one epoch, at the head:
each prune re-derives it, and :meth:`verify_full` rejects an epoch anywhere
else — a re-anchor in mid-chain would let a cut-out segment verify.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path

__all__ = [
    "Chain",
]


def _h(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _canon(obj) -> str:
    return json.dumps(
        obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def _link_fp(prev: str, payload: dict, state: dict) -> str:
    # hash covers prev + payload + full state snapshot (state is no longer
    # trusted-but-unsealed: forging the latest link's state now breaks the chain)
    blob = prev + "|" + _canon(payload) + "|" + _canon(state)
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


def _stamp(ts) -> float | None:
    """Epoch seconds for a ledger stamp; ``None`` when it cannot be read.

    Tolerates a ``Z`` suffix (which ``fromisoformat`` rejects before Python
    3.11) and never raises: a stamp the watchdog cannot read is kept, not fatal
    (2026-09-25 — one bad tombstone ``ts`` used to make every later patrol raise
    after appending its link).
    """
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def default_bite(row: dict, raw_line: str) -> str:
    """What gets sealed per row: the display text plus the fingerprint id.
    Everything else (heat, recalls, lineage...) is allowed to live."""
    fp_id = (row.get("fp") or {}).get("fp_id", "")
    text = row.get("text")
    marker = text if isinstance(text, str) else ("\x00" + raw_line)  # missing != empty
    return _h(str(marker) + "|fp:" + str(fp_id))


class Chain:
    """Watchdog over one or more jsonl stores.

    ``key_of`` names a row (default: its ``cid`` namespaced by the file, else a
    positional hash); ``bite`` decides what is sealed. ``on_alarm`` is called
    with a message when evaporation or mutation is detected. Rows without a
    stable ``cid`` fall back to a content key and are best-effort only — give
    every row a unique ``cid`` for real tamper-evidence."""

    def __init__(
        self,
        ledger: Path,
        watch: Sequence[Path],
        key_of: Callable[[dict, int, Path], str] | None = None,
        bite: Callable[[dict, str], str] = default_bite,
        on_alarm: Callable[[str], None] | None = None,
        keep_days: int = 60,
    ):
        self.ledger = ledger
        self.watch = list(watch)
        self.key_of = key_of or self._default_key
        self.bite = bite
        self.on_alarm = on_alarm or (lambda msg: None)
        self.keep_days = keep_days

    @staticmethod
    def _default_key(r: dict, i: int, p: Path) -> str:
        cid = r.get("cid")
        base = str(p.resolve())
        if isinstance(cid, str) and cid:
            return f"{base}::{cid}"
        return f"{base}::row{i}:{_h(_canon(r))[:8]}"

    # -- ledger plumbing ---------------------------------------------------- #
    def _lines(self):
        if not self.ledger.exists():
            return
        # Line feeds only, decoded per line; anything that is not a JSON object
        # is malformed (2026-09-25) — a torn multi-byte write or a stray list
        # used to raise out of every patrol and verify instead of failing it.
        for raw in self.ledger.read_bytes().split(b"\n"):
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                j = json.loads(line)
            except Exception:
                j = None
            yield j if isinstance(j, dict) else {"__malformed__": True}

    def _links(self):
        for j in self._lines():
            if isinstance(j, dict) and j.get("fp") and "payload" in j and "state" in j:
                yield j

    def last_link(self) -> dict | None:
        tail = None
        for j in self._links():
            tail = j
        return tail

    def legal_death_keys(self) -> set:
        """Row keys (cids) every tombstone in the current window vouches for."""
        keys: set = set()
        for j in self._lines():
            if isinstance(j, dict) and j.get("event") == "legal_death":
                keys.update(j.get("cids", []))
        return keys

    def _snapshot(self) -> dict:
        out: dict = {}
        for path in self.watch:
            if not path.exists():
                continue
            # Line feeds only, decoded per line (2026-09-25): a row whose text
            # carries U+2028 stays one row, and a torn multi-byte row is one raw
            # entry instead of a decode error that stops every patrol.
            for i, raw in enumerate(path.read_bytes().split(b"\n")):
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    r = None
                if not isinstance(r, dict):
                    out[f"{path.resolve()}::raw:{_h(line)[:8]}"] = _h(line)
                    continue
                out[self.key_of(r, i, path)] = self.bite(r, line)
        return out

    # -- the patrol ---------------------------------------------------------- #
    def patrol(self, rebaseline: bool = False) -> dict:
        """One round: snapshot, diff, chain, alarm if warranted. The set of
        lawful deaths this round is written into the (chained) payload."""
        prev = self.last_link()
        prev_fp = prev["fp"] if prev else "genesis"
        prev_state = prev.get("state", {}) if prev else {}
        cur = self._snapshot()

        if rebaseline:
            missing, mutated, lawful = [], [], []
        else:
            missing = sorted(k for k in prev_state if k not in cur)
            mutated = sorted(k for k in prev_state if k in cur and cur[k] != prev_state[k])
            lawful_keys = self.legal_death_keys()
            # match on the cid embedded in the row key (namespace-correct)
            lawful = sorted(k for k in missing if k.split("::")[-1] in lawful_keys)
            missing = [k for k in missing if k not in lawful]

        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "n": len(cur),
            "added": sorted(k for k in cur if k not in prev_state),
            "missing": missing,
            "mutated": mutated,
            "lawful": lawful,
        }
        link = {
            "fp": _link_fp(prev_fp, payload, cur),
            "prev": prev_fp,
            "payload": payload,
            "state": cur,
        }
        entry = json.dumps(link, ensure_ascii=False) + "\n"
        # A torn last write must not swallow this link (2026-09-25): glued onto
        # it, the new link would be one more malformed line.
        if self.ledger.exists() and self.ledger.stat().st_size > 0:
            with self.ledger.open("rb") as f:
                f.seek(-1, 2)
                if f.read(1) != b"\n":
                    entry = "\n" + entry
        with self.ledger.open("a", encoding="utf-8") as lf:
            lf.write(entry)

        if missing or mutated:
            self.on_alarm(
                f"memory chain alarm: {len(missing)} evaporated "
                f"{missing[:5]}, {len(mutated)} mutated {mutated[:5]}"
            )
        self._prune()
        return {
            "added": payload["added"],
            "missing": missing,
            "mutated": mutated,
            "lawful_deaths": lawful,
        }

    def verify_full(self) -> bool:
        """Replay the ledger. Every link's hash is recomputed over
        ``prev + payload + state`` and its stored ``prev`` must equal the
        previous link's computed hash. A malformed line fails. The first link
        must be a ``genesis`` root (hash recomputed) or an explicit ``epoch:``
        re-anchor (trusted as a self-describing checkpoint). An empty ledger is
        vacuously valid.

        The re-anchor is trusted only as the head (2026-09-25): a second epoch,
        or one after the first link, fails. An epoch accepted anywhere let a
        segment cut out of the middle verify — and it is also how an honest
        ledger with a stack of stale epochs is told apart from a clean one."""
        prev = None
        seen_epoch = seen_link = False
        for j in self._lines():
            if j.get("__malformed__"):
                return False
            if j.get("epoch"):
                # explicit re-anchor: trusted checkpoint committing to the pruned
                # tail (an unavoidable feature of a rolling window, made honest)
                if seen_epoch or seen_link or not isinstance(j.get("fp"), str) or not j["fp"]:
                    return False
                seen_epoch = True
                prev = j["fp"]
                continue
            if not (j.get("fp") and "payload" in j and "state" in j):
                continue  # event lines (tombstones) are not chain links
            try:
                if _link_fp(j["prev"], j["payload"], j["state"]) != j["fp"]:
                    return False
            except (KeyError, TypeError, ValueError):
                return False  # a link without a usable prev/payload/state
            if prev is None:
                if j["prev"] != "genesis":
                    return False  # unpruned chain must start at genesis
            elif j["prev"] != prev:
                return False  # broken chain: prev doesn't match predecessor
            prev = j["fp"]
            seen_link = True
        return True

    def _prune(self) -> None:
        """Keep the rolling window; re-anchor the first survivor as an epoch that
        commits to the last discarded link's hash (so the chain stays verifiable
        across pruning instead of orphaning its head).

        Exactly one epoch, at the head (2026-09-25). An epoch is stamped with
        the time of the prune that wrote it — later than the links it anchors —
        so it used to outlive them: the next prune cut those links, kept the
        stale epoch and stacked a new one on top, and from the second prune on
        an honest ledger failed :meth:`verify_full` for good. Old epochs are no
        longer carried along: the one anchor is re-derived from the first
        surviving link — the existing epoch if it already names that link, a new
        one only when this prune cut exactly the link the survivor names. A
        ledger that already carries a stack heals on the next rewrite.
        """
        if not self.ledger.exists():
            return
        cutoff = datetime.now(timezone.utc).timestamp() - self.keep_days * 86400
        raw = [ln for ln in self.ledger.read_bytes().split(b"\n") if ln.strip()]
        keep: list[bytes] = []
        epochs: list[tuple[dict, bytes]] = []
        first_link: dict | None = None
        dropped_last_fp = None
        for ln in raw:
            try:
                j = json.loads(ln.decode("utf-8"))
            except Exception:
                j = None
            if not isinstance(j, dict):
                keep.append(ln)  # malformed: evidence, never silently dropped
                continue
            if j.get("epoch"):
                epochs.append((j, ln))  # re-derived below, never carried blindly
                continue
            payload = j.get("payload")
            ts = (payload.get("ts") if isinstance(payload, dict) else None) or j.get("ts")
            stamp = _stamp(ts)
            if stamp is not None and stamp < cutoff:
                if j.get("fp") and "payload" in j:
                    dropped_last_fp = j["fp"]  # remember the tail we cut
                continue
            if first_link is None and j.get("fp") and "payload" in j and "state" in j:
                first_link = j
            keep.append(ln)
        cut = len(keep) + len(epochs) < len(raw)
        # Re-anchor without rewriting any survivor: an epoch whose fp equals the
        # first survivor's prev (the dropped predecessor's hash), so the surviving
        # chain still links intact and verify_full has a declared, tamper-explicit
        # starting point. None when the head was never cut.
        first_prev = first_link.get("prev") if first_link is not None else None
        anchor: bytes | None = None
        if first_prev and first_prev != "genesis":
            anchor = next((ln for j, ln in epochs if j.get("fp") == first_prev), None)
            if anchor is None and cut and dropped_last_fp == first_prev:
                anchor = json.dumps(
                    {
                        "epoch": True,
                        "fp": first_prev,
                        "prev": f"epoch:{dropped_last_fp}",
                        "ts": datetime.now(timezone.utc).isoformat(),
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
        head = [anchor] if anchor is not None else []
        if not cut and [ln for _, ln in epochs] == head and (not head or raw[0] == anchor):
            return  # nothing aged out, and the head already is the one epoch
        lines = head + keep
        tmp = self.ledger.with_suffix(".jsonl.tmp")
        tmp.write_bytes(b"\n".join(lines) + (b"\n" if lines else b""))
        tmp.replace(self.ledger)
