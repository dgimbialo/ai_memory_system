"""Conflict resolution helpers for the AI Memory System.

Provides ConflictResolver -- a high-level object that wraps MemoryEngine
and lets callers list, inspect, and resolve detected conflicts without
touching storage directly.

Supported actions
-----------------
supersede_a  Mark entry_a as superseded; entry_b remains active.
supersede_b  Mark entry_b as superseded; entry_a remains active.
merge        Create a new combined entry (union of decisions/files/functions),
             then supersede both originals.
dismiss      Remove the conflict record without changing either entry.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import MemoryEngine

VALID_ACTIONS = {"supersede_a", "supersede_b", "merge", "dismiss"}


class ConflictResolver:
    def __init__(self, engine: "MemoryEngine") -> None:
        self._engine = engine

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_unresolved(self) -> List[Dict[str, Any]]:
        """Return all conflict records enriched with full entry details."""
        conflicts = self._engine._read_conflicts()
        memory_map = self._build_memory_map()

        result = []
        for c in conflicts:
            entry_a = memory_map.get(c.get("entry_a", ""))
            entry_b = memory_map.get(c.get("entry_b", ""))

            if entry_a and entry_a.get("status") in ("superseded", "resolved"):
                continue
            if entry_b and entry_b.get("status") in ("superseded", "resolved"):
                continue

            result.append({
                "id":         c.get("id", ""),
                "reason":     c.get("reason", ""),
                "similarity": c.get("similarity", 0.0),
                "timestamp":  c.get("timestamp", ""),
                "entry_a":    entry_a or {"id": c.get("entry_a"), "error": "not found"},
                "entry_b":    entry_b or {"id": c.get("entry_b"), "error": "not found"},
            })
        return result

    def resolve(
        self,
        conflict_id: str,
        action: str,
        reason: str = "",
    ) -> Dict[str, Any]:
        """Resolve a conflict record.

        Parameters
        ----------
        conflict_id : str
            The id field of the conflict record in conflicts.json.
        action : str
            One of: supersede_a, supersede_b, merge, dismiss.
        reason : str
            Human-readable explanation stored in the activity log.
        """
        if action not in VALID_ACTIONS:
            raise ValueError(
                "Invalid action '{}'. Must be one of {}".format(
                    action, sorted(VALID_ACTIONS)
                )
            )

        conflicts = self._engine._read_conflicts()
        conflict = next((c for c in conflicts if c.get("id") == conflict_id), None)
        if conflict is None:
            raise KeyError("Conflict '{}' not found in conflicts.json".format(conflict_id))

        entry_a_id = conflict["entry_a"]
        entry_b_id = conflict["entry_b"]
        memory_map = self._build_memory_map()

        entry_a = memory_map.get(entry_a_id)
        entry_b = memory_map.get(entry_b_id)

        if entry_a is None:
            raise KeyError("Entry '{}' referenced by conflict not found".format(entry_a_id))
        if entry_b is None:
            raise KeyError("Entry '{}' referenced by conflict not found".format(entry_b_id))

        affected: List[str] = []
        merged_entry: Optional[Dict[str, Any]] = None

        if action == "supersede_a":
            self._engine.update_status(
                entry_a_id, "superseded",
                reason=reason or "superseded via conflict resolution {}".format(conflict_id),
            )
            affected = [entry_a_id]

        elif action == "supersede_b":
            self._engine.update_status(
                entry_b_id, "superseded",
                reason=reason or "superseded via conflict resolution {}".format(conflict_id),
            )
            affected = [entry_b_id]

        elif action == "merge":
            # Supersede both originals BEFORE creating the merged entry so that
            # add_memory conflict-detection does not flag the new entry against them.
            self._engine.update_status(
                entry_a_id, "superseded",
                reason="superseded for merge -- conflict {}".format(conflict_id),
            )
            self._engine.update_status(
                entry_b_id, "superseded",
                reason="superseded for merge -- conflict {}".format(conflict_id),
            )
            merged_entry = self._merge_entries(entry_a, entry_b, reason)
            affected = [entry_a_id, entry_b_id, merged_entry["id"]]

        elif action == "dismiss":
            affected = []

        self._remove_conflict(conflict_id)

        log_reason = "resolve conflict {} action={}".format(conflict_id, action)
        if reason:
            log_reason += " -- " + reason
        self._engine._log("resolve_conflict", [conflict_id] + affected, log_reason)

        return {
            "conflict_id":      conflict_id,
            "action":           action,
            "reason":           reason,
            "affected_entries": affected,
            "merged_entry":     merged_entry,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_memory_map(self) -> Dict[str, Dict[str, Any]]:
        return {e["id"]: e for e in self._engine._read_memory() if "id" in e}

    def _remove_conflict(self, conflict_id: str) -> None:
        conflicts = self._engine._read_conflicts()
        conflicts = [c for c in conflicts if c.get("id") != conflict_id]
        self._engine.storage.write("conflicts.json", conflicts)

    def _merge_entries(
        self,
        a: Dict[str, Any],
        b: Dict[str, Any],
        reason: str,
    ) -> Dict[str, Any]:
        base = a if a.get("confidence", 0) >= b.get("confidence", 0) else b
        other = b if base is a else a

        def _union(key: str) -> List[str]:
            seen: set = set()
            out: List[str] = []
            for item in list(a.get(key) or []) + list(b.get(key) or []):
                norm = str(item).strip()
                if norm and norm not in seen:
                    seen.add(norm)
                    out.append(norm)
            return out

        tags = _union("tags")
        if "merged" not in tags:
            tags.append("merged")

        cause = base.get("cause", "") or other.get("cause", "")
        if reason:
            cause = (cause + " | " if cause else "") + "Merged: " + reason

        payload: Dict[str, Any] = {
            "type":        base.get("type", "note"),
            "description": base.get("description", ""),
            "cause":       cause,
            "fix":         base.get("fix", "") or other.get("fix", ""),
            "files":       _union("files"),
            "functions":   _union("functions"),
            "decisions":   _union("decisions"),
            "status":      "active",
            "confidence":  max(
                float(a.get("confidence") or 0),
                float(b.get("confidence") or 0),
            ),
            "tags": tags,
        }

        result = self._engine.add_memory(payload)
        return result["entry"]
