"""Revert pattern detector for the AI Memory System.

Detects when the same function or file has been subject to repeated
add+revert cycles across multiple memory entries, which indicates an
"unstable" feature that the agent keeps implementing and undoing.

Algorithm
---------
1. Collect all active entries that share at least one function (or file
   as fallback) with the new entry.
2. Split them into ADD entries and REVERT entries by scanning description,
   cause, and fix fields for keyword sets.
3. Count distinct (add, revert) pairs on the shared surface.
4. If pairs >= UNSTABLE_THRESHOLD:
   - Tag ALL related entries (including the new one) with "unstable".
   - Return a RevertWarning describing the situation.

Keyword sets
------------
ADD keywords:    add, implement, enable, create, introduce, support, activate
REVERT keywords: revert, remove, disable, undo, rollback, rewritten, delete,
                 back out, backed out, removed, deleted
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import MemoryEngine

# Number of distinct add+revert pairs on the same surface before warning
UNSTABLE_THRESHOLD = 2

# Confidence penalty multiplier applied when an entry is newly tagged 'unstable'.
# Signals instability in ranking without dropping below the decay floor.
_UNSTABLE_CONFIDENCE_PENALTY: float = 0.85
_MIN_CONF_FLOOR: float = 0.40

_ADD_RE = re.compile(
    r"\b(add|added|implement|implemented|enable|enabled|create|created|"
    r"introduce|introduced|support|supports|activate|activated)\b",
    re.IGNORECASE,
)
_REVERT_RE = re.compile(
    r"\b(revert|reverted|remove|removed|disable|disabled|undo|undone|"
    r"rollback|rolled back|rewritten|delete|deleted|back.?out|backed.?out)\b",
    re.IGNORECASE,
)


@dataclass
class RevertWarning:
    unstable_functions: List[str]
    unstable_files: List[str]
    revert_count: int
    related_entry_ids: List[str]
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "unstable_functions": self.unstable_functions,
            "unstable_files":     self.unstable_files,
            "revert_count":       self.revert_count,
            "related_entry_ids":  self.related_entry_ids,
            "message":            self.message,
        }


class RevertDetector:
    def __init__(self, engine: "MemoryEngine") -> None:
        self._engine = engine

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(
        self,
        new_entry_dict: Dict[str, Any],
        all_entries: List[Dict[str, Any]],
    ) -> Optional[RevertWarning]:
        """Check whether the new entry triggers a revert pattern warning.

        Parameters
        ----------
        new_entry_dict : dict   The newly added entry (already in memory).
        all_entries    : list   All entries currently in memory (including new).

        Returns RevertWarning if pattern detected, else None.
        """
        new_funcs: Set[str] = _norm_set(new_entry_dict.get("functions") or [])
        new_files: Set[str] = _norm_set(new_entry_dict.get("files") or [])

        if not new_funcs and not new_files:
            return None

        # Collect candidates: entries (other than the new one) that share
        # at least one function or file with the new entry.
        new_id = new_entry_dict.get("id", "")
        candidates: List[Dict[str, Any]] = []
        for e in all_entries:
            if e.get("id") == new_id:
                continue
            # Skip superseded / resolved entries
            if e.get("status") in ("superseded", "resolved"):
                continue
            e_funcs = _norm_set(e.get("functions") or [])
            e_files = _norm_set(e.get("files") or [])
            if (new_funcs & e_funcs) or (new_files & e_files):
                candidates.append(e)

        if not candidates:
            return None

        # Classify new entry and candidates as ADD or REVERT
        new_text = _combined_text(new_entry_dict)
        new_is_add    = bool(_ADD_RE.search(new_text))
        new_is_revert = bool(_REVERT_RE.search(new_text))

        add_entries:    List[Dict[str, Any]] = []
        revert_entries: List[Dict[str, Any]] = []

        for e in candidates:
            t = _combined_text(e)
            is_add    = bool(_ADD_RE.search(t))
            is_revert = bool(_REVERT_RE.search(t))
            if is_revert:
                revert_entries.append(e)
            elif is_add:
                add_entries.append(e)

        if new_is_revert:
            revert_entries.append(new_entry_dict)
        elif new_is_add:
            add_entries.append(new_entry_dict)

        # Count pairs: each revert must follow at least one add.
        # We use len(min(adds, reverts)) as a conservative pair count.
        pair_count = min(len(add_entries), len(revert_entries))
        if pair_count < UNSTABLE_THRESHOLD:
            return None

        # Identify the shared unstable surface
        unstable_funcs: List[str] = sorted(
            new_funcs & _union_norm_sets(e.get("functions") or [] for e in candidates)
        )
        unstable_files: List[str] = sorted(
            new_files & _union_norm_sets(e.get("files") or [] for e in candidates)
        )

        # Tag all related entries as "unstable" (only those not already tagged)
        related_ids = [e["id"] for e in add_entries + revert_entries if e.get("id") != new_id]
        all_ids = related_ids + [new_id]

        # Optimisation: skip the write if every entry is already tagged unstable
        all_already_tagged = all(
            "unstable" in (e.get("tags") or [])
            for e in add_entries + revert_entries
            if e.get("id") in set(all_ids)
        ) and "unstable" in (new_entry_dict.get("tags") or [])

        if not all_already_tagged:
            self._tag_unstable(all_ids)

        surface = ", ".join(unstable_funcs) or ", ".join(unstable_files)
        newly_tagged_count = sum(
            1 for e in add_entries + revert_entries + [new_entry_dict]
            if "unstable" not in (e.get("tags") or [])
        )
        message = (
            "WARNING: '{}' has been added/reverted {} time(s). "
            "Tag 'unstable' applied to {} related entries. "
            "Consider resolving this design decision before implementing again."
        ).format(surface, pair_count, len(related_ids) + 1)

        return RevertWarning(
            unstable_functions=unstable_funcs,
            unstable_files=unstable_files,
            revert_count=pair_count,
            related_entry_ids=related_ids,
            message=message,
        )

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _tag_unstable(self, entry_ids: List[str]) -> None:
        """Add 'unstable' tag to all given entry ids in memory.

        Only entries that do not already carry the tag are modified.
        Newly tagged entries also have their confidence reduced by
        UNSTABLE_CONFIDENCE_PENALTY (×0.85) so that repeated add/revert
        churn is reflected in the ranking score.
        The activity log reports the count of *newly* tagged entries,
        not the total number of IDs passed in.
        """
        if not entry_ids:
            return
        memory = self._engine._read_memory()
        ids_set = set(entry_ids)
        newly_tagged: List[str] = []
        for e in memory:
            if e.get("id") in ids_set:
                tags: List[str] = e.get("tags") or []
                if "unstable" not in tags:
                    e["tags"] = tags + ["unstable"]
                    # Reduce confidence to signal instability — cap at MIN_CONFIDENCE
                    orig = float(e.get("confidence") or 0.5)
                    penalised = round(max(orig * _UNSTABLE_CONFIDENCE_PENALTY, _MIN_CONF_FLOOR), 4)
                    e["confidence"] = penalised
                    newly_tagged.append(e["id"])
        if newly_tagged:
            self._engine.storage.write("memory.json", memory)
            self._engine._log(
                "tag_unstable",
                newly_tagged,
                "revert pattern detected; tagged {} new entries as unstable".format(
                    len(newly_tagged)
                ),
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _combined_text(entry: Dict[str, Any]) -> str:
    parts = [
        entry.get("description") or "",
        entry.get("cause") or "",
        entry.get("fix") or "",
    ]
    return " ".join(p for p in parts if p).lower()


def _norm_set(items: List[str]) -> Set[str]:
    """Normalise a list of function/file names: lowercase, strip whitespace."""
    result: Set[str] = set()
    for item in items:
        # Function lists can contain space-separated names in one string
        for token in str(item).split():
            t = token.strip().lower()
            if t:
                result.add(t)
    return result


def _union_norm_sets(iterables) -> Set[str]:
    result: Set[str] = set()
    for items in iterables:
        result |= _norm_set(list(items))
    return result
