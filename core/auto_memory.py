"""Auto-memory: correlates file-system changes with Copilot activity.

When a file in a watched project changes:
1.  Pull the git diff (what lines changed).
2.  Look back in copilot_activity.json for the most recent chat event
    within a configurable time window (default 120 s).
3.  Extract a user prompt from that event (best-effort).
4.  Infer a memory entry type from the prompt and diff.
5.  Call MemoryEngine.add_memory() automatically.
6.  Re-render the markdown wiki.

This module is purely logic — the OS-level file watching is in watcher.py.
"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DATA_DIR_DEFAULT = Path(__file__).resolve().parent.parent / "data"

# How far back (seconds) we look in the Copilot activity log to find a
# recent conversation event to pair with a file change.
CORRELATION_WINDOW_SECONDS = 120

# File extensions we care about (skip binaries, lock-files, etc.)
TRACKED_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".css", ".scss", ".html",
    ".java", ".cs", ".go", ".rs", ".rb", ".swift", ".kt",
    ".json", ".yaml", ".yml", ".toml", ".env",
    ".sh", ".ps1", ".md",
}

# Ignore these paths/names entirely
IGNORED_PATTERNS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", "dist", "build",
    "data/wiki",  # our own output
}

# Keywords → memory entry type mapping
_TYPE_HINTS: List[Tuple[str, str]] = [
    (r"\b(fix|bug|error|crash|exception|issue|broken|fail)\b", "bug_fix"),
    (r"\b(add|implement|creat|build|feature|support|new)\b", "feature"),
    (r"\b(decid|choos|switch|migrat|replac|move)\b", "decision"),
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# ── git helpers ───────────────────────────────────────────────────────────────

def _git_diff_summary(project_path: Path, file_path: Path) -> str:
    """Return a brief (+N/-N lines) diff summary for file_path."""
    rel = file_path.relative_to(project_path)
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD", "--", str(rel)],
            capture_output=True,
            text=True,
            cwd=str(project_path),
            timeout=10,
        )
        diff_text = result.stdout or ""
        added = diff_text.count("\n+") - diff_text.count("\n+++")
        removed = diff_text.count("\n-") - diff_text.count("\n---")
        added = max(0, added)
        removed = max(0, removed)
        if added or removed:
            return f"+{added} / -{removed} lines in {rel}"
        # Try unstaged diff
        result2 = subprocess.run(
            ["git", "diff", "--", str(rel)],
            capture_output=True,
            text=True,
            cwd=str(project_path),
            timeout=10,
        )
        diff2 = result2.stdout or ""
        added2 = max(0, diff2.count("\n+") - diff2.count("\n+++"))
        removed2 = max(0, diff2.count("\n-") - diff2.count("\n---"))
        if added2 or removed2:
            return f"+{added2} / -{removed2} lines in {rel}"
        return f"modified {rel}"
    except Exception:
        return f"modified {rel}"


def _is_git_repo(project_path: Path) -> bool:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            cwd=str(project_path),
            timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


# ── Copilot log extraction ────────────────────────────────────────────────────

def _extract_user_prompt(event: Dict[str, Any]) -> str:
    """Best-effort: pull a human-readable user message from a log event dict."""
    # JSON-RPC conversation messages
    for key in ("message", "content", "text", "query", "prompt", "userMessage"):
        val = event.get(key)
        if isinstance(val, str) and len(val) > 10:
            return val.strip()[:300]
    # Nested structure: params.message, body.message, etc.
    for wrapper in ("params", "body", "data", "result"):
        sub = event.get(wrapper)
        if isinstance(sub, dict):
            for key in ("message", "content", "text", "query", "prompt"):
                val = sub.get(key)
                if isinstance(val, str) and len(val) > 10:
                    return val.strip()[:300]
    # If it's a raw log line
    if "raw" in event:
        return str(event["raw"])[:200]
    return ""


def _find_recent_copilot_event(
    data_dir: Path,
    since: datetime,
    window: int = CORRELATION_WINDOW_SECONDS,
) -> Optional[Dict[str, Any]]:
    """Return the most recent Copilot chat event within `window` seconds of `since`."""
    activity_path = data_dir / "copilot_activity.json"
    if not activity_path.exists():
        return None
    try:
        with activity_path.open("r", encoding="utf-8") as f:
            events: List[Dict[str, Any]] = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    cutoff = since - timedelta(seconds=window)
    best: Optional[Dict[str, Any]] = None
    best_ts: Optional[datetime] = None

    for ev in events:
        ts_str = ev.get("_scanned_at") or ev.get("timestamp", "")
        ts = _parse_iso(ts_str)
        if ts is None:
            continue
        if cutoff <= ts <= since:
            prompt = _extract_user_prompt(ev)
            if prompt and (best_ts is None or ts > best_ts):
                best = ev
                best_ts = ts
    return best


# ── type inference ────────────────────────────────────────────────────────────

def _infer_type(prompt: str, diff_summary: str) -> str:
    combined = (prompt + " " + diff_summary).lower()
    for pattern, entry_type in _TYPE_HINTS:
        if re.search(pattern, combined, re.IGNORECASE):
            return entry_type
    return "note"


# ── main public function ──────────────────────────────────────────────────────

class AutoMemory:
    """Correlates file changes with Copilot activity and writes memory entries."""

    def __init__(self, data_dir: str | Path | None = None):
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR_DEFAULT
        self._last_auto: Dict[str, datetime] = {}  # file → last auto-memory ts

    def should_track(self, file_path: Path) -> bool:
        """Return True if this file should trigger auto-memory."""
        if file_path.suffix.lower() not in TRACKED_EXTENSIONS:
            return False
        parts = set(file_path.parts)
        for ignored in IGNORED_PATTERNS:
            if any(ignored in part for part in file_path.parts):
                return False
        return True

    def on_file_changed(
        self,
        project_path: Path,
        file_path: Path,
        debounce_seconds: float = 3.0,
    ) -> Optional[Dict[str, Any]]:
        """Called when `file_path` changes.  Returns the created entry or None.

        Uses debounce: if the same file was processed within `debounce_seconds`,
        skips to avoid duplicate entries from rapid saves.
        """
        if not self.should_track(file_path):
            return None

        # Debounce
        now = datetime.now(timezone.utc)
        last = self._last_auto.get(str(file_path))
        if last and (now - last).total_seconds() < debounce_seconds:
            return None
        self._last_auto[str(file_path)] = now

        # Find a recent Copilot event
        copilot_event = _find_recent_copilot_event(self.data_dir, now)
        prompt = _extract_user_prompt(copilot_event) if copilot_event else ""

        # Build diff summary
        if _is_git_repo(project_path):
            diff_summary = _git_diff_summary(project_path, file_path)
        else:
            try:
                diff_summary = f"modified {file_path.relative_to(project_path)}"
            except ValueError:
                diff_summary = f"modified {file_path.name}"

        entry_type = _infer_type(prompt, diff_summary)

        # Build description
        if prompt:
            # Trim to first sentence / line
            first_line = re.split(r"[.\n]", prompt.strip())[0].strip()
            description = first_line[:120] or prompt[:120]
        else:
            description = f"Auto-detected change in {file_path.name}"

        try:
            rel_file = str(file_path.relative_to(project_path))
        except ValueError:
            rel_file = str(file_path)

        payload: Dict[str, Any] = {
            "type": entry_type,
            "description": description,
            "cause": f"Copilot chat activity" if prompt else "File change detected by watcher",
            "fix": diff_summary,
            "files": [rel_file],
            "status": "active",
            "confidence": 0.6 if prompt else 0.4,
            "tags": ["auto"],
        }

        # Write to memory engine
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from core.engine import MemoryEngine

        engine = MemoryEngine(self.data_dir)
        result = engine.add_memory(payload)

        # Refresh markdown wiki
        try:
            engine.render_wiki_md()
        except Exception:
            pass

        print(
            f"[auto_memory] ✅ entry created: {result['entry']['id']} "
            f"({entry_type}) — {description[:60]}"
        )
        return result
