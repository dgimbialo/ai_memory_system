"""Lint pass for the memory store.

Inspired by Karpathy's *LLM Wiki* idea — periodically health-check the
knowledge base.  The linter is read-only by default: it only reports issues.
Use ``--apply`` (CLI) to let it auto-fix safe categories (currently only
"resolve_stale_duplicates").

Checks
------
* **stale**             — `active` entries older than N days
* **low_confidence**    — confidence below a threshold
* **orphan**            — entries with no `files`, no `tags` and no conflicts
* **dangling_conflict** — `conflicts_with` refers to an unknown entry id
* **duplicate_pair**    — pairs with semantic similarity ≥ 0.9 not yet
                          flagged as conflicts (potential silent duplicates)
* **missing_fix**       — `bug_fix` entries with empty `fix`
* **missing_cause**     — `bug_fix` entries with empty `cause`
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .embeddings import similarity

DUPLICATE_SIM_THRESHOLD = 0.90


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        # Accepts trailing 'Z' or offsets
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _combined_text(e: Dict[str, Any]) -> str:
    return " ".join(
        filter(None, [e.get("description", ""), e.get("cause", ""), e.get("fix", "")])
    )


class Linter:
    """Read-only health-check pass over memory.json + conflicts.json."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)

    # ── IO ────────────────────────────────────────────────────────────────

    def _read(self, name: str, default: Any) -> Any:
        path = self.data_dir / name
        if not path.exists():
            return default
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return default

    # ── individual checks ─────────────────────────────────────────────────

    def _check_stale(
        self, memory: List[Dict[str, Any]], stale_days: int
    ) -> List[Dict[str, Any]]:
        cutoff = _now() - timedelta(days=stale_days)
        out: List[Dict[str, Any]] = []
        for e in memory:
            if e.get("status") != "active":
                continue
            ts = _parse_iso(e.get("timestamp", ""))
            if ts is None:
                continue
            if ts < cutoff:
                out.append({
                    "id": e.get("id"),
                    "type": e.get("type"),
                    "age_days": (_now() - ts).days,
                    "description": e.get("description", "")[:80],
                })
        return out

    def _check_low_confidence(
        self, memory: List[Dict[str, Any]], threshold: float
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for e in memory:
            try:
                c = float(e.get("confidence", 0.5))
            except (TypeError, ValueError):
                continue
            if c < threshold:
                out.append({
                    "id": e.get("id"),
                    "confidence": c,
                    "description": e.get("description", "")[:80],
                })
        return out

    def _check_orphans(
        self, memory: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for e in memory:
            if e.get("files") or e.get("tags") or e.get("conflicts_with"):
                continue
            out.append({
                "id": e.get("id"),
                "type": e.get("type"),
                "description": e.get("description", "")[:80],
            })
        return out

    def _check_dangling_conflicts(
        self, memory: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        ids = {e.get("id") for e in memory}
        out: List[Dict[str, Any]] = []
        for e in memory:
            for ref in e.get("conflicts_with") or []:
                if ref not in ids:
                    out.append({
                        "id": e.get("id"),
                        "missing_reference": ref,
                    })
        return out

    def _check_silent_duplicates(
        self,
        memory: List[Dict[str, Any]],
        known_pairs: set[Tuple[str, str]],
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        n = len(memory)
        for i in range(n):
            a = memory[i]
            ta = _combined_text(a)
            if not ta:
                continue
            for j in range(i + 1, n):
                b = memory[j]
                aid, bid = a.get("id"), b.get("id")
                if not aid or not bid:
                    continue
                key1 = (aid, bid)
                key2 = (bid, aid)
                if key1 in known_pairs or key2 in known_pairs:
                    continue
                if bid in (a.get("conflicts_with") or []):
                    continue
                tb = _combined_text(b)
                if not tb:
                    continue
                sim = similarity(ta, tb)
                if sim >= DUPLICATE_SIM_THRESHOLD:
                    out.append({
                        "entry_a": aid,
                        "entry_b": bid,
                        "similarity": round(float(sim), 4),
                    })
        return out

    def _check_missing_fields(
        self, memory: List[Dict[str, Any]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        missing_fix: List[Dict[str, Any]] = []
        missing_cause: List[Dict[str, Any]] = []
        for e in memory:
            if e.get("type") == "bug_fix":
                eid = e.get("id")
                if not (e.get("fix") or "").strip():
                    missing_fix.append({"id": eid, "description": e.get("description", "")[:80]})
                if not (e.get("cause") or "").strip():
                    missing_cause.append({"id": eid, "description": e.get("description", "")[:80]})
        return {"missing_fix": missing_fix, "missing_cause": missing_cause}

    # ── orchestration ────────────────────────────────────────────────────

    def run(
        self,
        stale_days: int = 180,
        low_confidence: float = 0.3,
    ) -> Dict[str, Any]:
        memory: List[Dict[str, Any]] = self._read("memory.json", [])
        conflicts: List[Dict[str, Any]] = self._read("conflicts.json", [])

        known_pairs: set[Tuple[str, str]] = {
            (c.get("entry_a", ""), c.get("entry_b", "")) for c in conflicts
        }

        stale = self._check_stale(memory, stale_days)
        low_conf = self._check_low_confidence(memory, low_confidence)
        orphans = self._check_orphans(memory)
        dangling = self._check_dangling_conflicts(memory)
        silent_dups = self._check_silent_duplicates(memory, known_pairs)
        missing = self._check_missing_fields(memory)

        total_issues = (
            len(stale) + len(low_conf) + len(orphans) + len(dangling)
            + len(silent_dups) + len(missing["missing_fix"])
            + len(missing["missing_cause"])
        )

        return {
            "summary": {
                "memory_entries": len(memory),
                "known_conflicts": len(conflicts),
                "total_issues": total_issues,
                "stale_days_threshold": stale_days,
                "low_confidence_threshold": low_confidence,
                "checked_at": _now().isoformat(),
            },
            "stale": stale,
            "low_confidence": low_conf,
            "orphans": orphans,
            "dangling_conflicts": dangling,
            "silent_duplicates": silent_dups,
            "missing_fix": missing["missing_fix"],
            "missing_cause": missing["missing_cause"],
        }
