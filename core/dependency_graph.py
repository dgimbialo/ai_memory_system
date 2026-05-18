"""Dependency graph for the AI Memory System.

Maintains directed links between memory entries:

    entry A  --depends_on-->  entry B

Meaning: "A builds upon / was informed by the decision in B".

Typical usage:
    bug_fix  --depends_on-->  decision   (the fix implements a design choice)
    feature  --depends_on-->  decision   (the feature follows an architectural decision)
    decision --depends_on-->  decision   (layered design choices)

Storage
-------
Links are stored directly on the entry dicts in memory.json:
    entry A: { ..., "depends_on": ["id_B"], ... }
    entry B: { ..., "required_by": ["id_A"], ... }

Both sides are updated atomically in a single storage write.

Auto-suggestion
---------------
When a new bug_fix or feature is added, the graph engine can suggest
likely depends_on links based on semantic similarity to existing decisions.
These are returned as suggestions only -- never auto-committed.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import MemoryEngine

# Minimum similarity for auto-suggesting a dependency link
AUTO_SUGGEST_THRESHOLD: float = 0.75

# Entry types that can be the SOURCE of a dependency (depends_on)
_DEPENDENT_TYPES: Set[str] = {"bug_fix", "feature", "note", "decision"}

# Entry types that are preferred TARGETS (decisions are the canonical anchors)
_ANCHOR_TYPES: Set[str] = {"decision"}


class DependencyGraph:
    def __init__(self, engine: "MemoryEngine") -> None:
        self._engine = engine

    # ------------------------------------------------------------------
    # Public API � link management
    # ------------------------------------------------------------------

    def add_link(self, from_id: str, to_id: str) -> Dict[str, Any]:
        """Create a directed link: from_id depends_on to_id.

        Updates both entries atomically:
            from_entry.depends_on += [to_id]
            to_entry.required_by  += [from_id]

        Returns a dict with the updated entries.
        Raises KeyError if either id is not found.
        Raises ValueError if the link already exists or would create a cycle.
        """
        memory = self._engine._read_memory()
        idx = {e["id"]: i for i, e in enumerate(memory) if "id" in e}

        if from_id not in idx:
            raise KeyError("Entry '{}' not found".format(from_id))
        if to_id not in idx:
            raise KeyError("Entry '{}' not found".format(to_id))
        if from_id == to_id:
            raise ValueError("An entry cannot depend on itself")

        from_entry = memory[idx[from_id]]
        to_entry   = memory[idx[to_id]]

        from_deps = list(from_entry.get("depends_on") or [])
        if to_id in from_deps:
            raise ValueError("Link {}->{} already exists".format(from_id, to_id))

        # Cycle detection: if to_id already depends on from_id (directly or transitively)
        if self._reachable(to_id, from_id, memory):
            raise ValueError(
                "Adding link {}->{} would create a cycle".format(from_id, to_id)
            )

        from_deps.append(to_id)
        from_entry["depends_on"] = from_deps

        to_req = list(to_entry.get("required_by") or [])
        if from_id not in to_req:
            to_req.append(from_id)
        to_entry["required_by"] = to_req

        self._engine.storage.write("memory.json", memory)
        self._engine._log(
            "add_dependency_link",
            [from_id, to_id],
            "{} depends_on {}".format(from_id, to_id),
        )
        return {
            "from_entry": from_entry,
            "to_entry":   to_entry,
            "link":       "{} -> {}".format(from_id, to_id),
        }

    def remove_link(self, from_id: str, to_id: str) -> Dict[str, Any]:
        """Remove a directed link: from_id no longer depends_on to_id."""
        memory = self._engine._read_memory()
        idx = {e["id"]: i for i, e in enumerate(memory) if "id" in e}

        if from_id not in idx or to_id not in idx:
            raise KeyError("One or both entry IDs not found")

        from_entry = memory[idx[from_id]]
        to_entry   = memory[idx[to_id]]

        from_deps = list(from_entry.get("depends_on") or [])
        if to_id not in from_deps:
            raise ValueError("Link {}->{} does not exist".format(from_id, to_id))
        from_deps.remove(to_id)
        from_entry["depends_on"] = from_deps

        to_req = list(to_entry.get("required_by") or [])
        if from_id in to_req:
            to_req.remove(from_id)
        to_entry["required_by"] = to_req

        self._engine.storage.write("memory.json", memory)
        self._engine._log(
            "remove_dependency_link",
            [from_id, to_id],
            "removed link {} -> {}".format(from_id, to_id),
        )
        return {"removed_link": "{} -> {}".format(from_id, to_id)}

    # ------------------------------------------------------------------
    # Public API � traversal
    # ------------------------------------------------------------------

    def get_dependencies(
        self, entry_id: str, depth: int = 1
    ) -> Dict[str, Any]:
        """Return the dependency subgraph rooted at entry_id.

        Parameters
        ----------
        entry_id : str   The entry whose dependencies to resolve.
        depth    : int   How many levels to follow depends_on links (default 1).
                         depth=0 returns only the entry itself.
                         depth=-1 follows all the way to the roots (full transitive).

        Returns
        -------
        dict with keys:
            entry          -- the root entry dict (concise)
            dependencies   -- list of directly depended-on entries (concise)
            required_by    -- list of entries that directly depend on this one
            transitive     -- flat list of all transitive dependency IDs (if depth != 1)
        """
        mem_map = {e["id"]: e for e in self._engine._read_memory() if "id" in e}
        entry = mem_map.get(entry_id)
        if entry is None:
            raise KeyError("Entry '{}' not found".format(entry_id))

        direct_dep_ids = list(entry.get("depends_on") or [])
        direct_req_ids = list(entry.get("required_by") or [])

        dependencies = [_concise(mem_map[i]) for i in direct_dep_ids if i in mem_map]
        required_by  = [_concise(mem_map[i]) for i in direct_req_ids if i in mem_map]

        transitive: List[str] = []
        if depth != 1:
            max_depth = None if depth == -1 else depth
            transitive = list(self._transitive_deps(entry_id, mem_map, max_depth))

        return {
            "entry":        _concise(entry),
            "dependencies": dependencies,
            "required_by":  required_by,
            "transitive":   transitive,
        }

    def suggest_links(
        self,
        entry_id: str,
        threshold: float = AUTO_SUGGEST_THRESHOLD,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """Suggest potential depends_on links for entry_id.

        Finds existing decision entries whose text is semantically similar
        to entry_id. Returns ranked suggestions (not auto-committed).

        Only suggests entries of type 'decision' as targets.
        Does not suggest entries that are already linked or superseded.
        """
        from .embeddings import embed, cosine

        memory = self._engine._read_memory()
        mem_map = {e["id"]: e for e in memory if "id" in e}
        entry = mem_map.get(entry_id)
        if entry is None:
            raise KeyError("Entry '{}' not found".format(entry_id))

        already_linked = set(entry.get("depends_on") or [])
        entry_text = _full_text(entry)
        entry_vec  = embed(entry_text)

        suggestions = []
        for e in memory:
            eid = e.get("id", "")
            if eid == entry_id:
                continue
            if e.get("type") not in _ANCHOR_TYPES:
                continue
            if e.get("status") in ("superseded", "resolved"):
                continue
            if eid in already_linked:
                continue

            sim = cosine(entry_vec, embed(_full_text(e)))
            if sim >= threshold:
                suggestions.append({
                    "target_id":   eid,
                    "type":        e.get("type", ""),
                    "description": (e.get("description") or "")[:120],
                    "similarity":  round(sim, 4),
                    "decisions":   (e.get("decisions") or [])[:2],
                })

        suggestions.sort(key=lambda x: x["similarity"], reverse=True)
        return suggestions[:top_k]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _reachable(
        self,
        start_id: str,
        target_id: str,
        memory: List[Dict[str, Any]],
    ) -> bool:
        """Return True if target_id is reachable from start_id via depends_on."""
        mem_map = {e["id"]: e for e in memory if "id" in e}
        visited: Set[str] = set()
        queue = [start_id]
        while queue:
            current = queue.pop()
            if current == target_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            for dep in (mem_map.get(current) or {}).get("depends_on") or []:
                queue.append(dep)
        return False

    def _transitive_deps(
        self,
        entry_id: str,
        mem_map: Dict[str, Any],
        max_depth: Optional[int],
        _current_depth: int = 0,
        _visited: Optional[Set[str]] = None,
    ) -> Set[str]:
        if _visited is None:
            _visited = set()
        if entry_id in _visited:
            return set()
        _visited.add(entry_id)
        if max_depth is not None and _current_depth >= max_depth:
            return set()

        result: Set[str] = set()
        for dep_id in (mem_map.get(entry_id) or {}).get("depends_on") or []:
            result.add(dep_id)
            result |= self._transitive_deps(
                dep_id, mem_map, max_depth, _current_depth + 1, _visited
            )
        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _concise(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id":          entry.get("id", ""),
        "type":        entry.get("type", ""),
        "status":      entry.get("status", ""),
        "description": (entry.get("description") or "")[:120],
        "depends_on":  entry.get("depends_on") or [],
        "required_by": entry.get("required_by") or [],
        "confidence":  entry.get("confidence", 0),
    }


def _full_text(entry: Dict[str, Any]) -> str:
    decisions_text = " ".join(entry.get("decisions") or [])
    parts = [
        entry.get("description") or "",
        entry.get("fix") or "",
        decisions_text,
    ]
    return " ".join(p for p in parts if p)
