"""Copilot activity logger.

Discovers VS Code's GitHub Copilot chat debug-log directories and tails them
for new events.  New events are appended to ``data/copilot_activity.json``.

Cursor positions per log file are persisted in
``data/.copilot_log_cursors.json`` so the scanner never re-reads old content.

Platform support: Windows (%APPDATA%\\Code), Linux (~/.config/Code),
macOS (~/Library/Application Support/Code).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional


# ── VS Code data-dir discovery ────────────────────────────────────────────────

def _vscode_user_data_dirs() -> List[Path]:
    """Return existing VS Code 'User' data directories for the current OS."""
    candidates: List[Path] = []
    if os.name == "nt":  # Windows
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            candidates.append(Path(appdata) / "Code" / "User")
    else:
        home = Path.home()
        candidates += [
            home / ".config" / "Code" / "User",                          # Linux
            home / "Library" / "Application Support" / "Code" / "User",  # macOS
        ]
    return [p for p in candidates if p.exists()]


def find_copilot_log_dirs() -> List[Path]:
    """Return all GitHub.copilot-chat debug-log directories on this machine."""
    log_dirs: List[Path] = []
    for user_dir in _vscode_user_data_dirs():
        ws_storage = user_dir / "workspaceStorage"
        if not ws_storage.exists():
            continue
        for ws in ws_storage.iterdir():
            if not ws.is_dir():
                continue
            copilot_logs = ws / "GitHub.copilot-chat" / "debug-logs"
            if copilot_logs.exists():
                log_dirs.append(copilot_logs)
    return log_dirs


# ── Log line parsing ──────────────────────────────────────────────────────────

def _parse_log_line(line: str) -> Optional[Dict[str, Any]]:
    """Try to parse a single log line into a structured dict."""
    line = line.strip()
    if not line:
        return None

    # Attempt JSON first (Copilot often emits JSON-RPC objects)
    try:
        data = json.loads(line)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass

    # Common VS Code log format: [ISO-TIMESTAMP] [LEVEL] message
    m = re.match(
        r"\[?(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[^\]]*)\]?"
        r"\s+\[?(\w+)\]?\s+(.*)",
        line,
    )
    if m:
        return {
            "timestamp": m.group(1).strip(),
            "level": m.group(2).strip(),
            "message": m.group(3).strip(),
        }

    # Fallback: preserve raw text
    return {"raw": line}


_CHAT_KEYWORDS = frozenset([
    "conversation", "chat", "request", "response",
    "message", "prompt", "completion", "turn", "copilot",
    "inline", "suggestion", "agent",
])


def _is_chat_event(entry: Dict[str, Any]) -> bool:
    """Return True when the entry likely represents a Copilot interaction."""
    text = json.dumps(entry, ensure_ascii=False).lower()
    return any(kw in text for kw in _CHAT_KEYWORDS)


# ── Main logger class ─────────────────────────────────────────────────────────

class CopilotActivityLogger:
    """Scans VS Code Copilot debug logs and appends new events to storage."""

    ACTIVITY_FILE = "copilot_activity.json"
    CURSOR_FILE = ".copilot_log_cursors.json"

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._activity_path = self.data_dir / self.ACTIVITY_FILE
        self._cursor_path = self.data_dir / self.CURSOR_FILE
        if not self._activity_path.exists():
            self._write_activity([])

    # ── cursor management ─────────────────────────────────────────────────

    def _read_cursors(self) -> Dict[str, int]:
        if self._cursor_path.exists():
            try:
                with self._cursor_path.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _write_cursors(self, cursors: Dict[str, int]) -> None:
        with self._cursor_path.open("w", encoding="utf-8") as f:
            json.dump(cursors, f, indent=2)

    # ── activity storage ──────────────────────────────────────────────────

    def _read_activity(self) -> List[Dict[str, Any]]:
        try:
            with self._activity_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def _write_activity(self, activity: List[Dict[str, Any]]) -> None:
        with self._activity_path.open("w", encoding="utf-8") as f:
            json.dump(activity, f, indent=2, ensure_ascii=False)

    def _append_events(self, events: List[Dict[str, Any]]) -> None:
        if not events:
            return
        activity = self._read_activity()
        activity.extend(events)
        self._write_activity(activity)

    # ── scanning ──────────────────────────────────────────────────────────

    def _iter_new_lines(
        self, log_file: Path, cursors: Dict[str, int]
    ) -> Iterator[str]:
        """Yield lines from log_file that have not been read yet."""
        key = str(log_file)
        offset = cursors.get(key, 0)
        try:
            size = log_file.stat().st_size
            if size < offset:
                offset = 0  # file was rotated / truncated
            with log_file.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(offset)
                for line in f:
                    yield line
                cursors[key] = f.tell()
        except OSError:
            return

    def scan_once(self) -> int:
        """Single scan pass across all Copilot log dirs.

        Returns the number of new chat events recorded.
        """
        log_dirs = find_copilot_log_dirs()
        cursors = self._read_cursors()
        new_events: List[Dict[str, Any]] = []

        for log_dir in log_dirs:
            for log_file in sorted(log_dir.rglob("*")):
                if not log_file.is_file():
                    continue
                for line in self._iter_new_lines(log_file, cursors):
                    entry = _parse_log_line(line)
                    if entry and _is_chat_event(entry):
                        entry["_source_file"] = str(log_file)
                        entry["_scanned_at"] = datetime.now(timezone.utc).isoformat()
                        new_events.append(entry)

        self._write_cursors(cursors)
        self._append_events(new_events)
        return len(new_events)

    def watch(
        self,
        interval_seconds: float = 5.0,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
        stop_after: Optional[int] = None,
    ) -> None:
        """Poll for new Copilot activity continuously.

        Args:
            interval_seconds: seconds between scans.
            on_event: optional callback called for each new event dict.
            stop_after: stop automatically after this many scan iterations
                        (useful for testing).
        """
        import time

        scans = 0
        print(
            f"[copilot_logger] Watching Copilot activity "
            f"(interval={interval_seconds}s) — press Ctrl-C to stop."
        )
        try:
            while True:
                count = self.scan_once()
                if count:
                    print(f"[copilot_logger] +{count} new event(s) recorded.")
                    if on_event:
                        activity = self._read_activity()
                        for ev in activity[-count:]:
                            on_event(ev)
                scans += 1
                if stop_after is not None and scans >= stop_after:
                    break
                time.sleep(interval_seconds)
        except KeyboardInterrupt:
            print("\n[copilot_logger] Stopped by user.")

    # ── convenience helpers ───────────────────────────────────────────────

    def recent(self, n: int = 20) -> List[Dict[str, Any]]:
        """Return the last *n* recorded events."""
        return self._read_activity()[-n:]

    def stats(self) -> Dict[str, Any]:
        """Return a short summary suitable for status displays."""
        activity = self._read_activity()
        return {
            "total_events": len(activity),
            "log_dirs_found": len(find_copilot_log_dirs()),
            "activity_file": str(self._activity_path),
        }
