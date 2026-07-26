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

Threat model — what this does and does not catch. The chain is an unkeyed
SHA-256 chain stored beside the data it protects, so it detects **accidental
corruption and naive edits**, not an adversary with write access to the ledger:
such an adversary can recompute the whole chain, or append a ``legal_death``
tombstone for a memory they then delete and the disappearance reads as lawful.
The fail-safe direction is the honest half of the guarantee — *deleting* a real
tombstone alarms, *forging* one does not. ``verify_full`` attests link
integrity, never "no memory evaporated"; that question is what :meth:`patrol`
answers, and only for as long as its own ledger has not been rewritten.
Treat it as a smoke detector, not a lock. Keyed sealing (HMAC with a secret the
store cannot read, or an off-box append-only sink) is what would change this,
and is deliberately out of scope here.

An unexplained loss is also *sticky*: it stays in each subsequent link's
``unresolved`` list and keeps alarming until an operator passes
``rebaseline=True``. It used to be a one-shot notification — the round after an
alarm re-anchored the baseline and reported ``missing: []``, so a real
evaporation quietly looked as though it had resolved itself.

Retention: links and tombstones age out after ``keep_days``. Pruning does not
orphan the chain — the first surviving link is re-anchored as an **epoch**
whose ``prev`` commits to the hash of the last discarded link, so
:meth:`verify_full` has an explicit, self-describing starting point instead of
a link whose predecessor is gone.
"""

from __future__ import annotations

import hashlib
import json
import warnings
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
        # No handler must not mean no alarm. The default swallowed the one
        # notification a patrol ever emits, so a deployment that simply forgot
        # to pass on_alarm looked identical to one where nothing ever went
        # wrong. A warning is the quietest thing that still leaves a trace.
        self.on_alarm = on_alarm or self._warn_alarm
        self.keep_days = keep_days

    @staticmethod
    def _warn_alarm(msg: str) -> None:
        warnings.warn(msg, RuntimeWarning, stacklevel=2)

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
                row = json.loads(line)
            except Exception:
                yield {"__malformed__": True}
                continue
            # A tamper-evidence reader must not be crashable by the tampering it
            # is meant to detect. json.loads happily returns a list or a number,
            # and every consumer here calls .get() on what it receives: one
            # appended "[]" raised AttributeError out of verify_full() and
            # patrol(), permanently disabling the watchdog instead of alarming.
            # Anything that is not an object is malformed, which is the signal
            # verify_full already knows how to fail on.
            yield row if isinstance(row, dict) else {"__malformed__": True}

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
        # An unexplained loss stays on the books until someone clears it.
        # Previously the next link's state simply became the new baseline, so
        # the round after an alarm reported missing=[] and the evaporation
        # became invisible — a one-shot notification, and if the handler was
        # the (silent) default nothing was left at all.
        prev_unresolved = list((prev or {}).get("payload", {}).get("unresolved", []))
        cur = self._snapshot()

        if rebaseline:
            missing, mutated, lawful = [], [], []
            prev_unresolved = []  # explicit operator acknowledgement
        else:
            missing = sorted(k for k in prev_state if k not in cur)
            mutated = sorted(k for k in prev_state if k in cur and cur[k] != prev_state[k])
            lawful_keys = self.legal_death_keys()
            # match on the cid embedded in the row key (namespace-correct)
            lawful = sorted(k for k in missing if k.split("::")[-1] in lawful_keys)
            missing = [k for k in missing if k not in lawful]

        # Anything still gone (or gone again) carries forward; anything that
        # came back drops off by itself.
        unresolved = sorted({*prev_unresolved, *missing} - set(cur))
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "n": len(cur),
            "added": sorted(k for k in cur if k not in prev_state),
            "missing": missing,
            "mutated": mutated,
            "lawful": lawful,
            "unresolved": unresolved,
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
        elif unresolved:
            # Still unexplained from an earlier round. Keep saying so; silence
            # here is what made a real loss look like it had resolved itself.
            self.on_alarm(
                f"memory chain alarm (unresolved): {len(unresolved)} still "
                f"evaporated {unresolved[:5]} — pass rebaseline=True to accept"
            )
        self._prune()
        return {
            "added": payload["added"],
            "missing": missing,
            "mutated": mutated,
            "lawful_deaths": lawful,
            "unresolved": unresolved,
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
            if not isinstance(j, dict):
                keep.append(ln)  # not a record; leave it for verify_full to fail on
                continue
            payload = j.get("payload") if isinstance(j.get("payload"), dict) else {}
            ts = payload.get("ts", "") or j.get("ts", "")
            # ``ts`` on a tombstone comes from the caller (reaper's now_iso is a
            # free-text argument), so it is not guaranteed to be ISO. A bare
            # fromisoformat here meant one odd stamp raised ValueError out of
            # every later patrol() — the watchdog dying on data it was supposed
            # to police. An unparseable stamp simply is not old enough to prune.
            old = False
            if ts:
                try:
                    old = datetime.fromisoformat(str(ts)).timestamp() < cutoff
                except (ValueError, TypeError):
                    old = False
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
