"""feltstate.memory.lifecycle — checkable memory provenance and an auditable
forgetting lifecycle.

Three things are usually left open in agent memory layers: provenance attached
to each stored fact, deletion that propagates through the consolidations
derived from a fact, and physical deletion rather than an invalidation flag.
This package is an attempt at all three, as a set of small, composable organs:

* :mod:`.fingerprint` — every memory is born with a sealed birth record:
  source pointers + the affect of the moment + a UTC timestamp, hashed into an
  id. Edit the sealed record and the id stops verifying; the id itself is kept
  honest by the ledger below, which bites it into the chain. Genealogy lives
  outside the seal, so pruning ancestry is a life event, not forgery.
* :mod:`.clocks` — kinds of memory age at different speeds (trauma slowest,
  warmth slower, plain facts fastest); no floors. Exactly two deliberate
  immortality rules exist — the permanent line here, and the mercy rule in
  :mod:`.gc` — and both are declared, not accidents of arithmetic.
* :mod:`.gc` — the judge: authority flows downhill (a living distilled memory
  shields the facts it grew from), heritage is not life-support, source
  material is reference-counted, and whatever has no fingerprint can never be
  killed. Pure computation, returns a death plan.
* :mod:`.reaper` — the executioner: tombstone first, then delete from the live
  store and every snapshot it is given, with a replayable pending ledger for
  crash recovery. Source-material rows are marked, not yet physically purged —
  that adapter is future work.
* :mod:`.chain` — the witness: a hash-linked ledger that alarms on evaporation
  or mutation of what it seals, and can tell a lawful (tombstoned) death from
  a silent one.
* :mod:`.consistency` — the immune gate: a zero-LLM heuristic check that rejects
  unsupported distilled summaries before they enter the store. It is a lexical
  guardrail, not semantic proof, and its language tables are replaceable.
* :mod:`.smelt` — the furnace core: consistency gate → born salience → sealed
  fingerprint. It is pure and storage-agnostic; the caller owns clustering,
  summary generation and persistence.
* :mod:`.drill` — the genealogy walk: follow ``lineage`` and ``src`` back to raw
  source pointers, preserve evidence from pruned branches, and optionally resolve
  those pointers across their full transcript range with a caller-owned loader;
  ``trace_memory`` combines the tree, context, affect trail, and optional exact
  source verification into one report.

Together they aim to close a loop most memory layers leave open: a memory here
has a checkable birth record, an honest old age, and — when its time comes — a
death that the live store and its snapshots both respect. The one designed
exception is the **instinct memory**: a fused memory whose ancestors all died
keeps the copied source pointers in its own body; it can no longer say which
memory that part came *through*, even though the raw pointers remain until the
source archive itself expires.
"""

from .chain import Chain
from .clocks import ClockConfig, current_intensity
from .consistency import (
    ACCEPT,
    DEFAULT_CONFIG,
    REJECT,
    SUSPECT,
    ConsistencyConfig,
    check_consistency,
)
from .drill import affect_trail, drill, leaf_pointers, trace_contexts, trace_memory
from .fingerprint import (
    FingerprintError,
    core_hash,
    fuse,
    make_fingerprint,
    make_source_ptr,
    prune_lineage,
    verify_fingerprint,
    verify_source_ptr,
)
from .gc import DEFAULT_DEATH_LINE, GCError, is_collectable, resolve_deaths
from .reaper import ReaperError, execute, replay_if_pending
from .smelt import SmeltConfig, born_heat, smelt

__all__ = [
    "make_source_ptr",
    "verify_source_ptr",
    "make_fingerprint",
    "verify_fingerprint",
    "fuse",
    "prune_lineage",
    "core_hash",
    "FingerprintError",
    "ClockConfig",
    "current_intensity",
    "DEFAULT_DEATH_LINE",
    "is_collectable",
    "resolve_deaths",
    "GCError",
    "execute",
    "replay_if_pending",
    "ReaperError",
    "Chain",
    "check_consistency",
    "ConsistencyConfig",
    "DEFAULT_CONFIG",
    "ACCEPT",
    "SUSPECT",
    "REJECT",
    "drill",
    "leaf_pointers",
    "trace_contexts",
    "trace_memory",
    "affect_trail",
    "smelt",
    "SmeltConfig",
    "born_heat",
]
