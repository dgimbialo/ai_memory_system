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

    def query_memory(
        self,
        query: str,
        top_k: int = 5,
        filter_file: Optional[str] = None,
        filter_function: Optional[str] = None,
        fmt: str = "concise",
    ) -> List[Dict[str, Any]]:
        """Semantic search over memory entries.

        Args:
            query:           Natural-language query string.
            top_k:           Max results to return.
            filter_file:     If set, only return entries touching files that contain
                             this substring (e.g. 'RecordCorrection').
            filter_function: If set, only return entries where the functions list
                             contains this substring.
            fmt:             'concise' returns a lightweight summary focused on
                             decisions and description; 'full' returns the full entry.
        """
        memory = self._read_memory()

        # Apply file / function pre-filters
        if filter_file:
            memory = [
                e for e in memory
                if any(filter_file.lower() in f.lower() for f in e.get("files", []))
            ]
        if filter_function:
            memory = [
                e for e in memory
                if any(filter_function.lower() in fn.lower()
                       for fn in e.get("functions", []))
            ]

        scored: List[tuple[float, Dict[str, Any]]] = []
        for e in memory:
            # Index over description + cause + fix + decisions for richer matching
            decisions_text = " ".join(e.get("decisions", []))
            text = " ".join(filter(None, [
                e.get("description", ""),
                e.get("cause", ""),
                e.get("fix", ""),
                decisions_text,
            ]))
            score = similarity(query, text)
            scored.append((score, e))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:top_k]

        if fmt == "concise":
            return [
                {
                    "score":       round(float(s), 4),
                    "id":          e["id"],
                    "type":        e.get("type", ""),
                    "status":      e.get("status", ""),
                    "description": e.get("description", ""),
                    "decisions":   e.get("decisions", []),
                    "fix_summary": (e.get("fix") or "")[:200],
                    "files":       e.get("files", []),
                    "functions":   e.get("functions", []),
                    "tags":        [t for t in e.get("tags", [])
                                    if not t.startswith("project:")],
                }
                for s, e in top
            ]

        # fmt == 'full'
        return [{"score": round(float(s), 4), "entry": e} for s, e in top]

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


    # ---------- update copilot-instructions.md ----------

    def update_instructions(
        self,
        project_path: str,
        min_confidence: float = 0.80,
        dry_run: bool = False,
    ) -> dict:
        """Generate a Learned Patterns section from high-confidence decisions
        and write it into the project's .github/copilot-instructions.md.

        Only entries with:
          - status == "active"
          - confidence >= min_confidence
          - at least one non-empty decision string
        are included.

        Args:
            project_path:   Root of the project (must contain .github/).
            min_confidence: Minimum confidence score to include (default 0.80).
            dry_run:        If True, return the generated content without writing.

        Returns a dict with keys: patterns_count, content, updated (bool).
        """
        from pathlib import Path as _Path
        from .vscode_infra import VSCodeInfraBuilder

        memory = self._read_memory()

        # Collect decisions from qualifying entries
        patterns: list[str] = []
        for e in memory:
            if e.get("status") not in ("active",):
                continue
            if float(e.get("confidence", 0)) < min_confidence:
                continue
            decisions = e.get("decisions") or []
            for d in decisions:
                d = d.strip()
                if d and d not in patterns:
                    patterns.append(d)

        if not patterns:
            return {
                "patterns_count": 0,
                "content": "",
                "updated": False,
                "reason": "no qualifying decisions found (min_confidence={})".format(min_confidence),
            }

        # Build section content
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        lines = [
            "<!-- Auto-generated from AI Memory System decisions on {} -->".format(now),
            "",
        ]
        for p in patterns:
            # Normalise to a single-line bullet
            single = " ".join(p.split())
            lines.append("- {}".format(single))
        lines.append("")
        section_content = "\n".join(lines)

        if dry_run:
            return {
                "patterns_count": len(patterns),
                "content": section_content,
                "updated": False,
                "dry_run": True,
            }

        builder = VSCodeInfraBuilder(self.storage.data_dir)
        updated = builder.update_instructions(
            project_path, "Learned Patterns", section_content
        )

        self._log(
            "update_instructions",
            [],
            "wrote {} pattern(s) to {}" .format(len(patterns), project_path),
        )

        return {
            "patterns_count": len(patterns),
            "content": section_content,
            "updated": updated,
            "project_path": project_path,
        }

    # ---------- session summary ----------
    def session_summary(
        self,
        description: str,
        tags: Optional[List[str]] = None,
        since_n: int = 20,
    ) -> Dict[str, Any]:
        """Create a summary entry that captures the most recent session work.

        Reads the last *since_n* entries, collects all unique files/functions
        touched, and creates a single "note" type entry that summarises the
        session.  This is the recommended end-of-session command.

        Args:
            description: One-line summary of what the session accomplished.
            tags:        Optional extra tags.
            since_n:     How many recent entries to aggregate over.

        Returns a dict with the created entry and any conflicts detected.
        """
        memory = self._read_memory()
        recent = sorted(
            memory,
            key=lambda e: e.get("timestamp", ""),
            reverse=True,
        )[:since_n]

        # Aggregate files & functions touched in the session
        agg_files: list[str] = []
        agg_functions: list[str] = []
        for e in recent:
            for f in e.get("files", []):
                if f not in agg_files:
                    agg_files.append(f)
            for fn in e.get("functions", []):
                if fn not in agg_functions:
                    agg_functions.append(fn)

        # Build an auto-cause from descriptions of recent entries
        causes = [e.get("description", "")[:80] for e in recent[:5] if e.get("description")]
        auto_cause = "; ".join(causes) if causes else "end-of-session aggregation"

        payload: Dict[str, Any] = {
            "type": "note",
            "description": description,
            "cause": auto_cause,
            "fix": f"Session covered {len(recent)} entr(ies) and {len(agg_files)} file(s).",
            "files": agg_files[:30],
            "functions": agg_functions[:30],
            "decisions": [],
            "confidence": 0.9,
            "tags": (tags or []) + ["session-summary"],
        }
        result = self.add_memory(payload)
        self._log(
            "session_summary",
            [result["entry"]["id"]],
            f"aggregated {len(recent)} recent entries over {len(agg_files)} files",
        )
        return result
