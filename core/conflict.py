"""Conflict detection between memory entries."""
from __future__ import annotations

from typing import List, Tuple, Dict, Any

from .models import MemoryEntry, ConflictRecord
from .embeddings import similarity

# Words that indicate opposing actions / decisions
OPPOSITES = [
    ("enable", "disable"),
    ("add", "remove"),
    ("install", "uninstall"),
    ("use", "avoid"),
    ("allow", "block"),
    ("accept", "reject"),
    ("activate", "deactivate"),
    ("turn on", "turn off"),
    ("include", "exclude"),
    ("keep", "delete"),
    ("increase", "decrease"),
    ("show", "hide"),
]

SIM_DUPLICATE_THRESHOLD = 0.82
SIM_RELATED_THRESHOLD = 0.55


def _shared_files(a: MemoryEntry, b: MemoryEntry) -> List[str]:
    return sorted(set(a.files) & set(b.files))


def _opposite_terms(text_a: str, text_b: str) -> str | None:
    ta, tb = text_a.lower(), text_b.lower()
    for x, y in OPPOSITES:
        if (x in ta and y in tb) or (y in ta and x in tb):
            return f"opposing terms: '{x}' vs '{y}'"
    return None


def _combined_text(e: MemoryEntry) -> str:
    return " ".join(filter(None, [e.description, e.cause, e.fix]))


def detect_conflict(a: MemoryEntry, b: MemoryEntry) -> ConflictRecord | None:
    """Return a ConflictRecord if a and b conflict, else None."""
    if a.id == b.id:
        return None

    text_a = _combined_text(a)
    text_b = _combined_text(b)
    sim = similarity(text_a, text_b)
    shared = _shared_files(a, b)
    reasons: List[str] = []

    # Rule 1: contradictory fixes affecting same file(s)
    if shared and a.type in {"bug_fix", "decision"} and b.type in {"bug_fix", "decision"}:
        if a.fix and b.fix and similarity(a.fix, b.fix) < 0.4:
            reasons.append(
                f"contradictory fixes on shared files {shared}"
            )

    # Rule 2: semantically opposite solutions
    opp = _opposite_terms(text_a, text_b)
    if opp and (shared or sim >= SIM_RELATED_THRESHOLD):
        reasons.append(f"semantically opposite ({opp})")

    # Rule 3: unresolved duplicate issues
    if (
        sim >= SIM_DUPLICATE_THRESHOLD
        and a.status != "resolved"
        and b.status != "resolved"
        and a.type == b.type
    ):
        reasons.append(f"duplicate unresolved issue (similarity={sim:.2f})")

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
    out: List[ConflictRecord] = []
    seen: set[Tuple[str, str]] = set()
    for i, a in enumerate(entries):
        for b in entries[i + 1 :]:
            key = tuple(sorted((a.id, b.id)))
            if key in seen:
                continue
            seen.add(key)
            c = detect_conflict(a, b)
            if c:
                out.append(c)
    return out
