"""Conflict detection between memory entries.

Rules
-----
1. Contradictory fixes on shared files  (bug_fix / decision types only).
2. Semantically opposite solutions - suppressed for same work-stream entries.
3. Duplicate unresolved issues (very high similarity, same type).

v2 improvements
---------------
- SIM_RELATED_THRESHOLD raised 0.55 -> 0.70 (requires stronger signal).
- step-N tags recognised as sequential phases (same as phase-N).
- _same_work_stream() suppresses false positives for refactor chains.
- session-summary entries are never flagged as conflicts.
"""
from __future__ import annotations

import re
from typing import List, Tuple

from .models import MemoryEntry, ConflictRecord
from .embeddings import similarity

# ---------------------------------------------------------------------------
# Opposite-action word pairs
# ---------------------------------------------------------------------------
OPPOSITES = [
    ("enable",   "disable"),
    ("add",      "remove"),
    ("install",  "uninstall"),
    ("use",      "avoid"),
    ("allow",    "block"),
    ("accept",   "reject"),
    ("activate", "deactivate"),
    ("turn on",  "turn off"),
    ("include",  "exclude"),
    ("keep",     "delete"),
    ("increase", "decrease"),
    ("show",     "hide"),
]

# Thresholds
SIM_DUPLICATE_THRESHOLD = 0.82
SIM_RELATED_THRESHOLD   = 0.70   # raised from 0.55

# Detects phaseN / phase-N / stepN / step-N in tags and descriptions
_STEP_RE = re.compile(r"(?:phase|step)[-_]?([0-9]+)", re.IGNORECASE)

# Tag prefixes that signal membership in the same work-stream
_STREAM_PREFIXES: Tuple[str, ...] = (
    "midi-", "bar-", "grace-", "cross-", "recording",
    "correction", "pipeline", "refactor", "tick-",
    "phase", "step", "crash", "end-recording",
)
_GENERIC_TAGS = frozenset({"agent", "auto"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _shared_files(a: MemoryEntry, b: MemoryEntry) -> List[str]:
    return sorted(set(a.files) & set(b.files))


def _opposite_terms(text_a: str, text_b: str) -> str | None:
    ta, tb = text_a.lower(), text_b.lower()
    for x, y in OPPOSITES:
        if (x in ta and y in tb) or (y in ta and x in tb):
            return "opposing terms: '{}' vs '{}'".format(x, y)
    return None


def _combined_text(e: MemoryEntry) -> str:
    return " ".join(filter(None, [e.description, e.cause, e.fix]))


def _step_number(entry: MemoryEntry) -> int | None:
    for tag in entry.tags:
        m = _STEP_RE.search(tag)
        if m:
            return int(m.group(1))
    m = _STEP_RE.search(entry.description)
    return int(m.group(1)) if m else None


def _are_sequential_steps(a: MemoryEntry, b: MemoryEntry) -> bool:
    pa, pb = _step_number(a), _step_number(b)
    if pa is None or pb is None:
        return False
    return abs(pa - pb) <= 2


def _is_stream_tag(tag: str) -> bool:
    return any(tag == p.rstrip("-") or tag.startswith(p) for p in _STREAM_PREFIXES)


def _same_work_stream(a: MemoryEntry, b: MemoryEntry) -> bool:
    """True when entries are complementary parts of the same work-stream."""
    tags_a = {t for t in a.tags if t not in _GENERIC_TAGS and not t.startswith("project:")}
    tags_b = {t for t in b.tags if t not in _GENERIC_TAGS and not t.startswith("project:")}

    # Direct shared non-generic tag
    if tags_a & tags_b:
        return True

    # One carries a step/phase tag AND both have stream-area tags
    stream_a = {t for t in tags_a if _is_stream_tag(t)}
    stream_b = {t for t in tags_b if _is_stream_tag(t)}
    has_step = any(_STEP_RE.search(t) for t in list(a.tags) + list(b.tags))
    if has_step and stream_a and stream_b:
        return True

    return False


# ---------------------------------------------------------------------------
# Core detection
# ---------------------------------------------------------------------------

def detect_conflict(a: MemoryEntry, b: MemoryEntry) -> ConflictRecord | None:
    if a.id == b.id:
        return None

    # Skip resolved/superseded entries
    if a.status in {"resolved", "superseded"} or b.status in {"resolved", "superseded"}:
        return None

    # Session summaries are informational, never conflicting
    if "session-summary" in a.tags or "session-summary" in b.tags:
        return None

    text_a = _combined_text(a)
    text_b = _combined_text(b)
    sim    = similarity(text_a, text_b)
    shared = _shared_files(a, b)
    reasons: List[str] = []

    # Rule 1: contradictory fixes on shared files
    if shared and a.type in {"bug_fix", "decision"} and b.type in {"bug_fix", "decision"}:
        if a.fix and b.fix and a.description and b.description:
            fix_sim  = similarity(a.fix, b.fix)
            desc_sim = similarity(a.description, b.description)
            opp_fix  = _opposite_terms(a.fix, b.fix)
            # Both low fix similarity AND high description similarity required:
            # two entries must be about the SAME problem with opposite solutions.
            if fix_sim < 0.30 and desc_sim > 0.50 and opp_fix:
                reasons.append("contradictory fixes on shared files {}".format(shared))

    # Rule 2: semantically opposite solutions
    opp = _opposite_terms(text_a, text_b)
    # Require real semantic similarity even if files are shared.
    # Raised from 0.62 → 0.72 to reduce false positives on hot files
    # that appear in many unrelated entries (e.g. a core file touched by
    # the majority of bug fixes in the project).
    _min_opp_sim = 0.72
    if opp and sim >= _min_opp_sim:
        if _are_sequential_steps(a, b):
            pass  # sequential steps legitimately add/remove
        elif _same_work_stream(a, b):
            pass  # complementary parts of the same feature
        else:
            reasons.append("semantically opposite ({})".format(opp))

    # Rule 3: duplicate unresolved issues
    if (
        sim >= SIM_DUPLICATE_THRESHOLD
        and a.status != "resolved"
        and b.status != "resolved"
        and a.type == b.type
    ):
        reasons.append("duplicate unresolved issue (similarity={:.2f})".format(sim))

    if not reasons:
        return None

    return ConflictRecord(
        entry_a=a.id,
        entry_b=b.id,
        reason="; ".join(reasons),
        similarity=round(float(sim), 4),
    )


def find_conflicts_for(
    new_entry: MemoryEntry, existing: List[MemoryEntry]
) -> List[ConflictRecord]:
    out: List[ConflictRecord] = []
    for other in existing:
        if other.id == new_entry.id:
            continue
        c = detect_conflict(new_entry, other)
        if c is not None:
            out.append(c)
    return out


def find_all_conflicts(entries: List[MemoryEntry]) -> List[ConflictRecord]:
    # Pre-compute file frequencies so detect_conflict can apply a hot-file discount.
    # A "hot file" is one referenced by more than HOT_FILE_PCT of all entries.
    # Conflicts detected only via shared hot files carry a weaker signal.
    HOT_FILE_PCT = 0.20
    total = len(entries)
    from collections import Counter
    file_counts: Counter = Counter(f for e in entries for f in e.files)
    hot_files = {f for f, c in file_counts.items() if total > 0 and c / total >= HOT_FILE_PCT}

    out: List[ConflictRecord] = []
    seen: set = set()
    for i, a in enumerate(entries):
        for b in entries[i + 1:]:
            key = tuple(sorted((a.id, b.id)))
            if key in seen:
                continue
            seen.add(key)
            c = detect_conflict(a, b)
            if c:
                # Suppress conflicts where the only shared files are hot files
                # (high-frequency files are a poor conflict signal).
                shared = _shared_files(a, b)
                if shared and all(f in hot_files for f in shared):
                    # Only suppress if it is NOT a duplicate (high-sim) conflict —
                    # duplicates remain regardless of file frequency.
                    if c.similarity < SIM_DUPLICATE_THRESHOLD:
                        continue
                out.append(c)
    return out
