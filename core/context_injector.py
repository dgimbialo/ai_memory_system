"""SessionStart hook — injects project memory context into every new agent session.

VS Code calls this script at the start of each agent session.
Input  (stdin): JSON with session metadata (may be empty).
Output (stdout): JSON with `systemMessage` containing a compact memory summary.

The summary includes:
- Recent active memory entries (last N, sorted by timestamp desc)
- Open conflicts
- Wiki index excerpt (top entries by file/type)

Exit code 0 always — never block the session.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "data"

# How many recent entries to show
RECENT_N = 10
# How many conflicts to show
CONFLICTS_N = 5
# Maximum total characters for systemMessage (keep context window impact small)
MAX_CHARS = 4000


def _detect_project_from_session(data: dict) -> str | None:
    """Try to read project from session metadata or cwd."""
    # VS Code may pass workspaceFolder or similar in future — check common keys
    for key in ("workspaceFolder", "workspacePath", "cwd", "projectPath"):
        val = data.get(key)
        if isinstance(val, str) and val:
            return Path(val).name
    # Fallback: check if there's a managed_projects.json and find current workspace
    try:
        managed_path = DATA_DIR / "managed_projects.json"
        if managed_path.exists():
            managed = json.loads(managed_path.read_text(encoding="utf-8"))
            # We can't know cwd here, so return None and use default store
    except Exception:
        pass
    return None


def _load_data(data_dir: Path) -> tuple[list, list]:
    """Load memory entries and conflicts from data_dir."""
    memory_path = data_dir / "memory.json"
    conflicts_path = data_dir / "conflicts.json"

    entries: list = []
    conflicts: list = []

    if memory_path.exists():
        try:
            raw = json.loads(memory_path.read_text(encoding="utf-8"))
            entries = raw if isinstance(raw, list) else []
        except Exception:
            pass

    if conflicts_path.exists():
        try:
            raw = json.loads(conflicts_path.read_text(encoding="utf-8"))
            conflicts = raw if isinstance(raw, list) else []
        except Exception:
            pass

    return entries, conflicts


def _format_entry(e: dict) -> str:
    ts = e.get("timestamp", "")[:10]
    eid = e.get("id", "?")[:8]
    etype = e.get("type", "note")
    desc = e.get("description", "")[:120]
    files = ", ".join(e.get("files") or [])[:60]
    conf = e.get("confidence", 0.0)
    status = e.get("status", "active")
    line = f"- [{eid}] ({etype}, conf={conf:.2f}, {status}) {desc}"
    if files:
        line += f"  → {files}"
    if ts:
        line += f"  [{ts}]"
    return line


def _build_summary(entries: list, conflicts: list, project: str | None) -> str:
    lines: list[str] = []

    project_label = project or "default"
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"## Project Memory — {project_label}  ({now_utc})")
    lines.append("")

    # Recent active entries
    active = [e for e in entries if e.get("status") == "active"]
    active_sorted = sorted(active, key=lambda e: e.get("timestamp", ""), reverse=True)
    recent = active_sorted[:RECENT_N]

    if recent:
        lines.append(f"### Recent Active Entries ({len(active)} total, showing {len(recent)})")
        for e in recent:
            lines.append(_format_entry(e))
        lines.append("")

    # Open conflicts
    open_conflicts = [c for c in conflicts if c.get("status") != "resolved"][:CONFLICTS_N]
    if open_conflicts:
        lines.append(f"### Open Conflicts ({len(open_conflicts)})")
        for c in open_conflicts:
            a = c.get("entry_a", "?")[:8]
            b = c.get("entry_b", "?")[:8]
            reason = c.get("reason", "")[:80]
            sim = c.get("similarity", 0.0)
            lines.append(f"- {a} ↔ {b}  sim={sim:.2f}  {reason}")
        lines.append("")

    # Stats
    total = len(entries)
    by_type: dict[str, int] = {}
    for e in entries:
        by_type[e.get("type", "note")] = by_type.get(e.get("type", "note"), 0) + 1
    stats_parts = [f"{k}={v}" for k, v in sorted(by_type.items())]
    lines.append(f"### Stats  total={total}  " + "  ".join(stats_parts))

    return "\n".join(lines)


def main() -> None:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        data = {}

    project = _detect_project_from_session(data)

    # Resolve data directory
    if project:
        slug = project.strip().lower().replace(" ", "_")
        data_dir = DATA_DIR / "projects" / slug
    else:
        data_dir = DATA_DIR

    entries, conflicts = _load_data(data_dir)

    if not entries and not conflicts:
        # Nothing stored yet — don't clutter the context
        print(json.dumps({"continue": True}))
        sys.exit(0)

    summary = _build_summary(entries, conflicts, project)

    # Truncate if too long
    if len(summary) > MAX_CHARS:
        summary = summary[:MAX_CHARS] + "\n\n... (truncated)"

    result = {
        "continue": True,
        "systemMessage": summary,
    }
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
