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

import bisect
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import MemoryEngine

# Default decay parameters
HALF_LIFE_DAYS: float = 60.0
# Decay floor. Kept *below* the birth confidence (0.5) so that a stale entry can
# actually decay and a reinforced entry can actually stand out — previously the
# floor (0.40) equalled the default birth value, which pinned 93% of real
# entries at one number and made confidence carry no information.
MIN_CONFIDENCE: float = 0.25

# Statuses exempt from decay (they are already terminal)
_EXEMPT_STATUS = {"superseded", "resolved"}

# How many days before decay even starts (entries younger than this are untouched)
GRACE_PERIOD_DAYS: float = 7.0

# ── Activity-relative aging ───────────────────────────────────────────────────
# Memory staleness is measured against how much the *project* has moved on, not
# against the calendar: each entry added after this one contributes
# DAYS_PER_EVENT of "effective age", capped by the wall-clock age. A project on
# pause therefore freezes its memory (nothing new was learned that could
# supersede it), while an actively-developed project ages entries as before.
# Real-world audit motivating this: fast_acquisition_device decayed to the
# floor during a 2-month pause, exactly when its memory was most needed.
DAYS_PER_EVENT: float = 3.0

# Decisions age 4x slower than other entries: a design decision holds until it
# is explicitly superseded, unlike a bug-fix note that goes stale naturally.
# Audit finding: all 13 piobmasterpro decisions had sunk to the 0.25 floor.
DECISION_HALF_LIFE_MULT: float = 4.0

# Every confirmed use of a memory extends its half-life by this fraction:
# hl_eff = hl * (1 + USAGE_HALF_LIFE_BONUS * usage_count). A one-off +delta on
# reinforce proved too weak against daily decay (audit #2: 30 reinforcements
# could not stop 72% of a store re-flooring) — proven-useful memories must rot
# structurally slower, not just start a little higher.
USAGE_HALF_LIFE_BONUS: float = 0.5


def activity_age_days(
    wall_age_days: float,
    newer_events: int,
    days_per_event: float = DAYS_PER_EVENT,
) -> float:
    """Effective age of a memory given project activity since it was written.

    ``newer_events`` is the number of entries added to the store after this
    entry's anchor timestamp. The effective age never exceeds the wall-clock
    age (a burst of activity cannot make memory older than it really is).
    """
    return min(float(wall_age_days), float(newer_events) * days_per_event)


def effective_confidence(
    original: float,
    timestamp: str,
    half_life_days: float = HALF_LIFE_DAYS,
    min_confidence: float = MIN_CONFIDENCE,
    now: Optional[datetime] = None,
    age_days_override: Optional[float] = None,
) -> float:
    """Compute the decayed confidence for a single entry.

    Parameters
    ----------
    original       : stored confidence value (0.0 - 1.0)
    timestamp      : ISO-8601 string of when the entry was last written
    half_life_days : days until confidence halves (default 60)
    min_confidence : absolute floor (default 0.40)
    now            : datetime to use as "now" (defaults to UTC now, injectable for tests)
    age_days_override : replace the wall-clock age with an activity-relative
                        age (see activity_age_days); timestamp must still parse

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

    if age_days_override is not None:
        age_days = float(age_days_override)

    if age_days < GRACE_PERIOD_DAYS:
        return float(original)

    factor = math.pow(0.5, age_days / half_life_days)
    decayed = float(original) * factor
    return max(decayed, float(min_confidence))


def entry_effective_confidence(
    entry: Dict[str, Any],
    sorted_timestamps: Optional[List[str]] = None,
    half_life_days: float = HALF_LIFE_DAYS,
    min_confidence: float = MIN_CONFIDENCE,
    now: Optional[datetime] = None,
) -> float:
    """Decayed confidence for a full entry dict — the one true formula.

    Combines every rule in one place so DecayEngine, query ranking and the
    context injector cannot drift apart:
      * anchor = freshest of ``last_used`` / ``timestamp`` (reuse resets clock)
      * activity-relative age when ``sorted_timestamps`` (all entries' write
        timestamps, ascending) is provided — otherwise wall-clock age
      * decisions decay DECISION_HALF_LIFE_MULT times slower
      * each confirmed use extends the half-life (USAGE_HALF_LIFE_BONUS)
    """
    anchor = _anchor_ts(entry)
    hl = half_life_days * (
        DECISION_HALF_LIFE_MULT if entry.get("type") == "decision" else 1.0
    )
    usage = int(entry.get("usage_count") or 0)
    if usage > 0:
        hl *= 1.0 + USAGE_HALF_LIFE_BONUS * usage

    age_override: Optional[float] = None
    if sorted_timestamps and anchor:
        if now is None:
            now = datetime.now(timezone.utc)
        try:
            ts = datetime.fromisoformat(anchor)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            wall_age = (now - ts).total_seconds() / 86400.0
            newer = len(sorted_timestamps) - bisect.bisect_right(sorted_timestamps, anchor)
            age_override = activity_age_days(wall_age, newer)
        except (ValueError, TypeError):
            age_override = None

    return effective_confidence(
        original=float(entry.get("confidence") or 0.5),
        timestamp=anchor,
        half_life_days=hl,
        min_confidence=min_confidence,
        now=now,
        age_days_override=age_override,
    )


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
        memory = self._engine._read_memory()
        sorted_ts = sorted(e.get("timestamp") or "" for e in memory)
        result = []
        for e in memory:
            orig = float(e.get("confidence") or 0.5)
            eff = self._eff(e, now, sorted_ts)
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
        sorted_ts = sorted(e.get("timestamp") or "" for e in memory)
        changes: List[Dict[str, Any]] = []
        changed_count = 0
        skipped_count = 0

        for e in memory:
            if e.get("status") in _EXEMPT_STATUS:
                skipped_count += 1
                continue

            orig = float(e.get("confidence") or 0.5)
            # Decay is always computed from an immutable base, never from the
            # already-decayed stored value — otherwise every apply() compounds
            # the previous one (a real store hit the floor within weeks because
            # a broken daily guard re-applied decay on every single add).
            base = float(e.get("confidence_base") or orig)
            eff = self._eff({**e, "confidence": base}, now, sorted_ts)
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
                if "confidence_base" not in e:
                    e["confidence_base"] = round(base, 4)
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

    def _eff(
        self,
        entry: Dict[str, Any],
        now: datetime,
        sorted_ts: Optional[List[str]] = None,
    ) -> float:
        return entry_effective_confidence(
            entry,
            sorted_timestamps=sorted_ts,
            half_life_days=self.half_life_days,
            min_confidence=self.min_confidence,
            now=now,
        )

    @staticmethod
    def _age_days(entry: Dict[str, Any], now: datetime) -> float:
        try:
            ts = datetime.fromisoformat(_anchor_ts(entry))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return (now - ts).total_seconds() / 86400.0
        except (ValueError, TypeError):
            return 0.0


def _anchor_ts(entry: Dict[str, Any]) -> str:
    """Decay clock anchor: the freshest of last_used and timestamp.

    Reusing a memory (recalled and acted upon) resets its decay clock, so a
    repeatedly-useful entry stays confident even if it was first written long
    ago. Falls back to the write timestamp when the entry was never reused.
    """
    last_used = entry.get("last_used") or ""
    timestamp = entry.get("timestamp") or ""
    if last_used and timestamp:
        return max(last_used, timestamp)  # ISO-8601 sorts lexicographically
    return last_used or timestamp
