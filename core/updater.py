"""Controlled updater: merges entries, refreshes wiki, preserves history."""
from __future__ import annotations

from typing import Dict, List, Any
from collections import defaultdict

from .models import MemoryEntry


class Updater:
    """Performs controlled, append-first updates to the in-memory state."""

    def merge_entry(
        self, memory: List[Dict[str, Any]], new_entry: MemoryEntry
    ) -> List[Dict[str, Any]]:
        """Append new entry. Reject if id already exists (no silent overwrite)."""
        existing_ids = {e.get("id") for e in memory}
        if new_entry.id in existing_ids:
            raise ValueError(f"Duplicate entry id '{new_entry.id}' — refusing to overwrite.")
        memory.append(new_entry.to_dict())
        return memory

    def update_status(
        self,
        memory: List[Dict[str, Any]],
        entry_id: str,
        status: str,
    ) -> Dict[str, Any]:
        for e in memory:
            if e.get("id") == entry_id:
                e["status"] = status
                return e
        raise KeyError(f"Entry '{entry_id}' not found")

    def update_confidence(
        self,
        memory: List[Dict[str, Any]],
        entry_id: str,
        confidence: float,
    ) -> Dict[str, Any]:
        if not (0.0 <= confidence <= 1.0):
            raise ValueError("confidence must be between 0.0 and 1.0")
        for e in memory:
            if e.get("id") == entry_id:
                e["confidence"] = float(confidence)
                return e
        raise KeyError(f"Entry '{entry_id}' not found")

    def mark_conflict(
        self,
        memory: List[Dict[str, Any]],
        entry_a: str,
        entry_b: str,
    ) -> None:
        for e in memory:
            if e.get("id") == entry_a:
                e["status"] = "conflict"
                rel = e.setdefault("conflicts_with", [])
                if entry_b not in rel:
                    rel.append(entry_b)
            elif e.get("id") == entry_b:
                e["status"] = "conflict"
                rel = e.setdefault("conflicts_with", [])
                if entry_a not in rel:
                    rel.append(entry_a)

    def rebuild_wiki(self, memory: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Group entries into wiki sections by type and by file."""
        by_type: Dict[str, List[str]] = defaultdict(list)
        by_file: Dict[str, List[str]] = defaultdict(list)
        by_status: Dict[str, List[str]] = defaultdict(list)

        for e in memory:
            eid = e.get("id")
            by_type[e.get("type", "note")].append(eid)
            by_status[e.get("status", "active")].append(eid)
            for f in e.get("files", []) or []:
                by_file[f].append(eid)

        return {
            "sections": {
                "by_type": {k: v for k, v in by_type.items()},
                "by_file": {k: v for k, v in by_file.items()},
                "by_status": {k: v for k, v in by_status.items()},
            },
            "entry_count": len(memory),
        }
