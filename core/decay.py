"""Confidence decay module for the AI Memory System.

Implements time-based confidence decay: the longer an entry goes without
being confirmed or updated, the lower its effective confidence becomes.

Decay formula
-------------
    effective = max(original * 0.5 ^ (age_days / HALF_LIFE_DAYS), MIN_CONFIDENCE)

With default parameters:
    HALF_LIFE_DAYS = 60   -- confidence halves every 60 days of silence
    MIN_CONFIDENCE = 0.40 -- floor: entries never drop below this

Examples (original=0.9):
    0 days  -> 0.900  (no change)
    30 days -> 0.636
    60 days -> 0.450
    90 days -> 0.318 -> clamped to 0.400
    180 days -> 0.400 (floor)

Ideal case (fresh entries): age_days == 0 -> factor == 1.0 -> no change.

Usage
-----
    from core.decay import DecayEngine
    de = DecayEngine(engine)

    # Read-only: returns entries with effective_confidence field added
    preview = de.preview()

    # Write: updates confidence in memory.json for stale entries
    result  = de.apply(dry_run=False)

    # Used internally by query_memory to re-rank by decayed confidence
    eff = de.effective_confidence(original=0.9, timestamp="2026-01-01T00:00:00+00:00")
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import MemoryEngine

# Default decay parameters
HALF_LIFE_DAYS: float = 60.0
MIN_CONFIDENCE: float = 0.40

# Statuses exempt from decay (they are already terminal)
_EXEMPT_STATUS = {"superseded", "resolved"}

# How many days before decay even starts (entries younger than this are untouched)
GRACE_PERIOD_DAYS: float = 7.0


def effective_confidence(
    original: float,
    timestamp: str,
    half_life_days: float = HALF_LIFE_DAYS,
    min_confidence: float = MIN_CONFIDENCE,
    now: Optional[datetime] = None,
) -> float:
    """Compute the decayed confidence for a single entry.

    Parameters
    ----------
    original       : stored confidence value (0.0 - 1.0)
    timestamp      : ISO-8601 string of when the entry was last written
    half_life_days : days until confidence halves (default 60)
    min_confidence : absolute floor (default 0.40)
    now            : datetime to use as "now" (defaults to UTC now, injectable for tests)

    Returns
    -------
    float in [min_confidence, original]
    """
    try:
        if now is None:
            now = datetime.now(timezone.utc)
        ts = datetime.fromisoformat(timestamp)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_days = (now - ts).total_seconds() / 86400.0
    except (ValueError, TypeError):
        return float(original)

    if age_days < GRACE_PERIOD_DAYS:
        return float(original)

    factor = math.pow(0.5, age_days / half_life_days)
    decayed = float(original) * factor
    return max(decayed, float(min_confidence))


class DecayEngine:
    """High-level decay controller attached to a MemoryEngine."""

    def __init__(
        self,
        engine: "MemoryEngine",
        half_life_days: float = HALF_LIFE_DAYS,
        min_confidence: float = MIN_CONFIDENCE,
    ) -> None:
        self._engine = engine
        self.half_life_days = half_life_days
        self.min_confidence = min_confidence

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def preview(self) -> List[Dict[str, Any]]:
        """Return all entries with an added ``effective_confidence`` field.

        This is a read-only operation: nothing is written to disk.
        Entries whose effective confidence equals their stored confidence
        are included with ``changed: false``.
        """
        now = datetime.now(timezone.utc)
        result = []
        for e in self._engine._read_memory():
            orig = float(e.get("confidence") or 0.5)
            eff = self._eff(e, now)
            result.append({
                "id":                   e.get("id", ""),
                "description":          (e.get("description") or "")[:120],
                "status":               e.get("status", ""),
                "original_confidence":  round(orig, 4),
                "effective_confidence": round(eff, 4),
                "changed":              round(eff, 4) != round(orig, 4),
                "age_days":             round(self._age_days(e, now), 1),
            })
        return result

    def apply(self, dry_run: bool = False) -> Dict[str, Any]:
        """Apply decay: write updated confidence values to memory.json.

        Only entries that are:
            - status NOT in {superseded, resolved}
            - age > GRACE_PERIOD_DAYS
            - effective_confidence < stored confidence (i.e. actually decayed)
        are modified.

        Parameters
        ----------
        dry_run : if True, compute changes but do NOT write to disk.

        Returns
        -------
        dict with keys:
            total_entries, changed_count, skipped_count, changes, dry_run
        """
        now = datetime.now(timezone.utc)
        memory = self._engine._read_memory()
        changes: List[Dict[str, Any]] = []
        changed_count = 0
        skipped_count = 0

        for e in memory:
            if e.get("status") in _EXEMPT_STATUS:
                skipped_count += 1
                continue

            orig = float(e.get("confidence") or 0.5)
            eff = self._eff(e, now)
            age = self._age_days(e, now)

            if age < GRACE_PERIOD_DAYS:
                skipped_count += 1
                continue

            if round(eff, 6) >= round(orig, 6):
                skipped_count += 1
                continue

            changes.append({
                "id":           e.get("id", ""),
                "description":  (e.get("description") or "")[:80],
                "old":          round(orig, 4),
                "new":          round(eff, 4),
                "age_days":     round(age, 1),
            })

            if not dry_run:
                e["confidence"] = round(eff, 4)
                changed_count += 1

        if not dry_run and changes:
            self._engine.storage.write("memory.json", memory)
            self._engine._log(
                "decay_confidence",
                [c["id"] for c in changes],
                "applied decay to {} entries (half_life={}d, min={})".format(
                    len(changes), self.half_life_days, self.min_confidence
                ),
            )

        return {
            "total_entries": len(memory),
            "changed_count": len(changes) if not dry_run else 0,
            "would_change":  len(changes),
            "skipped_count": skipped_count,
            "changes":       changes,
            "dry_run":       dry_run,
            "half_life_days": self.half_life_days,
            "min_confidence": self.min_confidence,
        }

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _eff(self, entry: Dict[str, Any], now: datetime) -> float:
        return effective_confidence(
            original=float(entry.get("confidence") or 0.5),
            timestamp=entry.get("timestamp") or "",
            half_life_days=self.half_life_days,
            min_confidence=self.min_confidence,
            now=now,
        )

    @staticmethod
    def _age_days(entry: Dict[str, Any], now: datetime) -> float:
        try:
            ts = datetime.fromisoformat(entry.get("timestamp") or "")
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return (now - ts).total_seconds() / 86400.0
        except (ValueError, TypeError):
            return 0.0
