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
a link whose predecessor is gone.
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
        for line in self.ledger.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                yield {"__malformed__": True}

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
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
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
        with self.ledger.open("a", encoding="utf-8") as lf:
            lf.write(json.dumps(link, ensure_ascii=False) + "\n")

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
        vacuously valid."""
        prev = None
        for j in self._lines():
            if j.get("__malformed__"):
                return False
            if j.get("epoch"):
                # explicit re-anchor: trusted checkpoint committing to the pruned
                # tail (an unavoidable feature of a rolling window, made honest)
                prev = j["fp"]
                continue
            if not (j.get("fp") and "payload" in j and "state" in j):
                continue  # event lines (tombstones) are not chain links
            if _link_fp(j["prev"], j["payload"], j["state"]) != j["fp"]:
                return False
            if prev is None:
                if j["prev"] != "genesis":
                    return False  # unpruned chain must start at genesis
            elif j["prev"] != prev:
                return False  # broken chain: prev doesn't match predecessor
            prev = j["fp"]
        return True

    def _prune(self) -> None:
        """Keep the rolling window; re-anchor the first survivor as an epoch that
        commits to the last discarded link's hash (so the chain stays verifiable
        across pruning instead of orphaning its head)."""
        if not self.ledger.exists():
            return
        cutoff = datetime.now(timezone.utc).timestamp() - self.keep_days * 86400
        raw = [ln for ln in self.ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
        keep, dropped_last_fp = [], None
        for ln in raw:
            try:
                j = json.loads(ln)
            except Exception:
                keep.append(ln)
                continue
            ts = j.get("payload", {}).get("ts", "") or j.get("ts", "")
            old = ts and datetime.fromisoformat(ts).timestamp() < cutoff
            if old:
                if j.get("fp") and "payload" in j:
                    dropped_last_fp = j["fp"]  # remember the tail we cut
                continue
            keep.append(ln)
        if len(keep) == len(raw):
            return  # nothing aged out
        # Re-anchor without rewriting any survivor: prepend an epoch marker whose
        # fp equals the first survivor's prev (the dropped predecessor's hash), so
        # the surviving chain still links intact and verify_full has a declared,
        # tamper-explicit starting point. Skip if the head wasn't actually cut.
        first_prev = None
        for ln in keep:
            try:
                j = json.loads(ln)
            except Exception:
                continue
            if j.get("fp") and "payload" in j and "state" in j:
                first_prev = j["prev"]
                break
        if first_prev and first_prev != "genesis":
            anchor = {
                "epoch": True,
                "fp": first_prev,
                "prev": f"epoch:{dropped_last_fp or 'genesis'}",
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            keep = [json.dumps(anchor, ensure_ascii=False)] + keep
        tmp = self.ledger.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
        tmp.replace(self.ledger)
