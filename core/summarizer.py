"""Auto file summary generator.

Builds a short plain-text overview for a source file based on the
memory entries that reference it.  No network calls — pure text analysis.

Storage
-------
Summaries are persisted in ``file_summaries.json`` inside each project's
data directory:

    {
        "path/to/file.cpp": "summary text ...",
        ...
    }

They are regenerated automatically after every ``add_memory()`` call
for each file touched by the new entry.
"""
from __future__ import annotations

from typing import Any, Dict, List

FILE_SUMMARIES = "file_summaries.json"

# Only look at this many most-recent entries per file (keeps summaries focused)
MAX_SUMMARY_ENTRIES: int = 20

# Confidence threshold for treating an entry as a key invariant
HIGH_CONFIDENCE: float = 0.75


class FileSummarizer:
    """Generate a 3-5 sentence summary for a source file from memory entries."""

    def summarize(self, file_path: str, entries: List[Dict[str, Any]]) -> str:
        """Build a plain-text summary from the entries that reference *file_path*.

        The summary has up to four parts:
        1. Entry count — how many records reference this file.
        2. Recent activity — descriptions of the latest few entries.
        3. Key invariants — decisions from ``decision``-typed or high-confidence entries.
        4. Open issues — active ``bug_fix`` entries (potential instability).

        Parameters
        ----------
        file_path : str
            The source file path (used only for display).
        entries   : list[dict]
            All memory entries that list *file_path* in their ``files`` field.

        Returns
        -------
        str
            Plain text, suitable for embedding at the top of a wiki file page
            or attaching to a ``query_memory`` result.
        """
        if not entries:
            return f"`{file_path}` — no memory entries recorded yet."

        # Sort newest-first, cap at MAX_SUMMARY_ENTRIES
        recent = sorted(
            entries,
            key=lambda e: e.get("timestamp", ""),
            reverse=True,
        )[:MAX_SUMMARY_ENTRIES]

        total = len(entries)
        parts: List[str] = []

        # ── 1. Entry count ──────────────────────────────────────────────────
        word = "entry" if total == 1 else "entries"
        parts.append(f"`{file_path}` has {total} memory {word}.")

        # ── 2. Recent activity ──────────────────────────────────────────────
        descriptions = _unique(
            e.get("description", "").strip()
            for e in recent
            if e.get("description", "").strip()
        )
        if descriptions:
            sample = "; ".join(descriptions[:3])
            parts.append(f"Recent activity: {sample}.")

        # ── 3. Key invariants ───────────────────────────────────────────────
        all_decisions: List[str] = []
        for e in recent:
            is_decision_type = e.get("type") == "decision"
            is_high_conf = float(e.get("confidence") or 0) >= HIGH_CONFIDENCE
            if is_decision_type or is_high_conf:
                for d in e.get("decisions") or []:
                    if d.strip():
                        all_decisions.append(d.strip())
        unique_decisions = _unique(all_decisions)
        if unique_decisions:
            sample = "; ".join(unique_decisions[:3])
            parts.append(f"Key decisions: {sample}.")

        # ── 4. Open issues ──────────────────────────────────────────────────
        open_bugs = _unique(
            e.get("description", "").strip()
            for e in recent
            if e.get("type") == "bug_fix"
            and e.get("status") == "active"
            and e.get("description", "").strip()
        )
        if open_bugs:
            sample = "; ".join(open_bugs[:2])
            parts.append(f"Open issues: {sample}.")

        return " ".join(parts)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _unique(iterable) -> List[str]:
    """Return items in order, deduplicated."""
    seen: set = set()
    result: List[str] = []
    for item in iterable:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
