"""Memory Engine — single controlled gateway for all memory operations."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import MemoryEntry, ConflictRecord
from .storage import Storage
from .updater import Updater
from .conflict import find_conflicts_for, find_all_conflicts
from .embeddings import similarity, backend as embed_backend
from .wiki_md import WikiRenderer


MEMORY_FILE = "memory.json"
WIKI_FILE = "wiki.json"
CONFLICTS_FILE = "conflicts.json"
LOG_FILE = "activity_log.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryEngine:
    def __init__(self, data_dir: str | Path):
        self.storage = Storage(data_dir)
        self.updater = Updater()
        self._ensure_initialized()

    # ---------- bootstrap helpers ----------
    def _ensure_initialized(self) -> None:
        if not self.storage.path(MEMORY_FILE).exists():
            self.storage.write(MEMORY_FILE, [])
        if not self.storage.path(WIKI_FILE).exists():
            self.storage.write(WIKI_FILE, {"sections": {}, "entry_count": 0})
        if not self.storage.path(CONFLICTS_FILE).exists():
            self.storage.write(CONFLICTS_FILE, [])
        if not self.storage.path(LOG_FILE).exists():
            self.storage.write(LOG_FILE, [])

    # ---------- internal IO ----------
    def _read_memory(self) -> List[Dict[str, Any]]:
        data = self.storage.read(MEMORY_FILE, default=[])
        return data if isinstance(data, list) else []

    def _read_conflicts(self) -> List[Dict[str, Any]]:
        data = self.storage.read(CONFLICTS_FILE, default=[])
        return data if isinstance(data, list) else []

    def _log(self, action: str, affected: List[str], reason: str) -> None:
        self.storage.append_log(
            LOG_FILE,
            {
                "timestamp": _now(),
                "action": action,
                "affected": affected,
                "reason": reason,
            },
        )

    # ---------- public API ----------
    def add_memory(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Validate, append, run conflict detection, refresh wiki."""
        entry = MemoryEntry.from_dict(payload) if not isinstance(payload, MemoryEntry) else payload
        entry.validate()

        memory = self._read_memory()
        existing_entries = [MemoryEntry.from_dict(e) for e in memory]

        self.updater.merge_entry(memory, entry)

        # Conflict detection
        conflicts = find_conflicts_for(entry, existing_entries)
        conflict_dicts = self._read_conflicts()
        for c in conflicts:
            self.updater.mark_conflict(memory, c.entry_a, c.entry_b)
            conflict_dicts.append(c.to_dict())

        self.storage.write(MEMORY_FILE, memory)
        if conflicts:
            self.storage.write(CONFLICTS_FILE, conflict_dicts)

        wiki = self.updater.rebuild_wiki(memory)
        self.storage.write(WIKI_FILE, wiki)

        self._log(
            action="add_memory",
            affected=[entry.id] + [c.entry_b for c in conflicts],
            reason=f"new {entry.type}; {len(conflicts)} conflict(s) detected",
        )

        return {
            "entry": entry.to_dict(),
            "conflicts": [c.to_dict() for c in conflicts],
        }

    def list_memory(
        self,
        type_: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        memory = self._read_memory()
        out = memory
        if type_:
            out = [e for e in out if e.get("type") == type_]
        if status:
            out = [e for e in out if e.get("status") == status]
        return out

    def detect_conflicts(self) -> List[Dict[str, Any]]:
        """Re-scan all memory and refresh conflicts.json."""
        memory = self._read_memory()
        entries = [MemoryEntry.from_dict(e) for e in memory]
        conflicts = find_all_conflicts(entries)

        # Apply conflict status (append-only relations)
        for c in conflicts:
            self.updater.mark_conflict(memory, c.entry_a, c.entry_b)

        existing = self._read_conflicts()
        existing_keys = {(c.get("entry_a"), c.get("entry_b")) for c in existing}
        for c in conflicts:
            key = (c.entry_a, c.entry_b)
            if key not in existing_keys and (c.entry_b, c.entry_a) not in existing_keys:
                existing.append(c.to_dict())

        self.storage.write(MEMORY_FILE, memory)
        self.storage.write(CONFLICTS_FILE, existing)

        self._log(
            action="detect_conflicts",
            affected=[c.entry_a for c in conflicts] + [c.entry_b for c in conflicts],
            reason=f"full scan; {len(conflicts)} conflict(s) total",
        )
        return [c.to_dict() for c in conflicts]

    def query_memory(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        memory = self._read_memory()
        scored: List[tuple[float, Dict[str, Any]]] = []
        for e in memory:
            text = " ".join(
                filter(None, [e.get("description", ""), e.get("cause", ""), e.get("fix", "")])
            )
            score = similarity(query, text)
            scored.append((score, e))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {"score": round(float(s), 4), "entry": e} for s, e in scored[:top_k]
        ]

    def update_status(self, entry_id: str, status: str, reason: str = "") -> Dict[str, Any]:
        memory = self._read_memory()
        updated = self.updater.update_status(memory, entry_id, status)
        self.storage.write(MEMORY_FILE, memory)
        self._log("update_status", [entry_id], reason or f"status -> {status}")
        return updated

    def update_confidence(
        self, entry_id: str, confidence: float, reason: str = ""
    ) -> Dict[str, Any]:
        memory = self._read_memory()
        updated = self.updater.update_confidence(memory, entry_id, confidence)
        self.storage.write(MEMORY_FILE, memory)
        self._log("update_confidence", [entry_id], reason or f"confidence -> {confidence}")
        return updated

    def state(self) -> Dict[str, Any]:
        memory = self._read_memory()
        wiki = self.storage.read(WIKI_FILE, default={})
        conflicts = self._read_conflicts()
        return {
            "embedding_backend": embed_backend(),
            "entry_count": len(memory),
            "conflict_count": len(conflicts),
            "wiki": wiki,
        }

    # ---------- markdown projection ----------
    def render_wiki_md(self) -> Dict[str, Any]:
        """Regenerate the markdown wiki under data/wiki/."""
        renderer = WikiRenderer(self.storage.data_dir)
        report = renderer.render_all()
        self._log(
            action="render_wiki_md",
            affected=[],
            reason=(
                f"rendered {report['entries_pages']} entry page(s), "
                f"{report['file_pages']} file page(s)"
            ),
        )
        return report
