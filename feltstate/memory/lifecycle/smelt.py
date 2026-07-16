"""feltstate.memory.lifecycle.smelt — forge a verified, traceable crystal from source rows.

The core of a private companion's crystallisation furnace, with the scheduling,
prompts, and storage stripped out — those are the caller's. Given the source rows
a memory is being distilled from and a candidate summary ``text`` (produced
however you like — an LLM call, a template, by hand), :func:`smelt`:

1. gates the text against its sources with :func:`.check_consistency` (zero-LLM):
   a ``reject`` is **not** committed (``crystal`` is ``None``; the sources are
   still live, so re-summarise and smelt again), a ``suspect`` is committed at a
   discounted heat and flagged;
2. computes a born *heat* (salience) from the sources' intensities — mean for a
   floor, peak for a lift, a small bonus per row, capped;
3. seals a birth fingerprint over the source pointers (+ the source ids as
   ``src`` lineage), so the crystal retains checkable source pointers and can be drilled back toward
   the original material while the caller-owned archive remains available (see
   :mod:`.drill`).

It does **not** decide *what* to crystallise or *when* — clustering and
scheduling are the caller's — and it writes nothing to disk. Fingerprinting is
fail-closed by default: unusable provenance rejects the crystal. Set
``SmeltConfig(require_fingerprint=False)`` only when an explicitly unsealed
fallback is preferable; the returned crystal then carries ``fingerprint=None``
and a ``fp_error`` note.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from .consistency import REJECT, SUSPECT, ConsistencyConfig, check_consistency
from .fingerprint import FingerprintError, make_fingerprint


@dataclass(frozen=True)
class SmeltConfig:
    """Heat curve + the consistency gate config. Defaults match the source line."""

    milestone_cap: float = 1.0
    default_cap: float = 0.9
    mean_weight: float = 0.6
    peak_weight: float = 0.4
    count_weight: float = 0.02
    default_intensity: float = 0.5
    consistency: ConsistencyConfig | None = None  # None → ConsistencyConfig()
    require_fingerprint: bool = True


def _intensity(row: Mapping | object) -> float | None:
    if not isinstance(row, Mapping):
        return None
    v = row.get("intensity")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return max(0.0, min(1.0, float(v)))


def born_heat(
    source_rows: Sequence[Mapping], *, milestone: bool = False, config: SmeltConfig | None = None
) -> float:
    """Salience a crystal is born at: ``mean*w1 + peak*w2 + count*w3``, capped.
    Rows without a usable ``intensity`` fall back to ``default_intensity``."""
    cfg = config or SmeltConfig()
    ints = [i for i in (_intensity(r) for r in source_rows) if i is not None]
    if not ints:
        ints = [max(0.0, min(1.0, cfg.default_intensity))]
    born = (
        cfg.mean_weight * (sum(ints) / len(ints))
        + cfg.peak_weight * max(ints)
        + cfg.count_weight * len(source_rows)
    )
    cap = cfg.milestone_cap if milestone else cfg.default_cap
    return round(max(0.0, min(max(0.0, cap), born)), 3)


def _validate_unsealed_fields(mid: str, birth_affect: Mapping, ts_utc: str) -> tuple[dict, str]:
    """Validate non-provenance fields before allowing an unsealed fallback.

    Opting out of a fingerprint relaxes only the source-pointer requirement; it
    must not allow malformed identity, non-finite affect, or ambiguous time into
    the store.
    """
    if not isinstance(mid, str) or not mid:
        raise FingerprintError("mid (unique memory id) is required and non-empty")
    if not isinstance(birth_affect, Mapping):
        raise FingerprintError("birth_affect must be a mapping")
    affect: dict[str, float] = {}
    for key, value in birth_affect.items():
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
        ):
            raise FingerprintError(
                f"birth_affect[{key!r}] must be a finite number, got {value!r}"
            )
        affect[str(key)] = float(value)
    if not isinstance(ts_utc, str) or not ts_utc:
        raise FingerprintError("ts must be a non-empty ISO-8601 string")
    try:
        dt = datetime.fromisoformat(ts_utc.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FingerprintError(f"ts is not ISO-8601: {ts_utc!r}") from exc
    if dt.tzinfo is None or dt.utcoffset() != timezone.utc.utcoffset(None):
        raise FingerprintError(f"ts must be timezone-aware UTC, got {ts_utc!r}")
    return affect, ts_utc


def smelt(
    text: str,
    source_rows: Sequence[Mapping],
    *,
    birth_affect: Mapping,
    ts_utc: str,
    mid: str,
    source_ptrs: Sequence[Mapping],
    src_ids: Sequence[str] | None = None,
    milestone: bool = False,
    config: SmeltConfig | None = None,
) -> dict:
    """Forge one crystal from ``source_rows`` + a caller-produced ``text``.

    Returns ``{"verdict": accept|suspect|reject, "fails": [...], "detail": {...},
    "crystal": {...} | None}``. On ``reject`` the crystal is ``None``. Otherwise ``crystal`` is
    ``{mid, text, heat, ts, birth_affect, fingerprint, suspect, n_sources}``.
    """
    cfg = config or SmeltConfig()
    rows = list(source_rows)

    vd = check_consistency(text, rows, cfg.consistency)
    if vd["verdict"] == REJECT:
        return {
            "verdict": REJECT,
            "fails": vd["fails"],
            "detail": vd["detail"],
            "crystal": None,
        }

    heat = born_heat(rows, milestone=milestone, config=cfg)
    if vd["verdict"] == SUSPECT:
        mult = (cfg.consistency or ConsistencyConfig()).suspect_heat_mult
        heat = round(heat * mult, 3)

    fingerprint: dict | None = None
    fp_error: str | None = None
    safe_affect: dict | None = None
    safe_ts: str | None = None
    try:
        fingerprint = make_fingerprint(
            source_ptrs,
            birth_affect,
            ts_utc,
            mid=mid,
            src=[str(s) for s in (src_ids or [])],
        )
    except FingerprintError as exc:
        fp_error = str(exc)
        if cfg.require_fingerprint:
            return {
                "verdict": REJECT,
                "fails": [*vd["fails"], "fingerprint"],
                "detail": vd["detail"],
                "fp_error": fp_error,
                "crystal": None,
            }
        try:
            safe_affect, safe_ts = _validate_unsealed_fields(mid, birth_affect, ts_utc)
        except FingerprintError as metadata_exc:
            return {
                "verdict": REJECT,
                "fails": [*vd["fails"], "birth-metadata"],
                "detail": vd["detail"],
                "fp_error": str(metadata_exc),
                "crystal": None,
            }

    if fingerprint is not None:
        safe_affect = dict(fingerprint["core"]["birth_affect"])
        safe_ts = str(fingerprint["core"]["ts"])
    assert safe_affect is not None and safe_ts is not None

    crystal = {
        "mid": mid,
        "text": text.strip(),
        "heat": heat,
        "ts": safe_ts,
        "birth_affect": safe_affect,
        "fingerprint": fingerprint,
        "fp_error": fp_error,
        "suspect": vd["fails"] or None,
        "n_sources": len(rows),
    }
    return {
        "verdict": vd["verdict"],
        "fails": vd["fails"],
        "detail": vd["detail"],
        "crystal": crystal,
    }
