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


def _eff_conf(entry: dict, now: datetime) -> float:
    """Decayed confidence, reuse-aware. Falls back to raw confidence if the
    decay module isn't importable (keeps the hook dependency-free)."""
    try:
        from .decay import effective_confidence
        anchor = entry.get("last_used") or entry.get("timestamp") or ""
        return effective_confidence(
            original=float(entry.get("confidence") or 0.5),
            timestamp=anchor,
            now=now,
        )
    except Exception:
        return float(entry.get("confidence") or 0.5)


def _build_summary(entries: list, conflicts: list, project: str | None) -> str:
    """Relevance-first session context.

    Earlier this injected the 10 *newest* active entries, so the agent saw
    whatever was edited last rather than what's most worth knowing. Now we lead
    with durable knowledge (decisions + high-confidence entries) and surface
    churn-prone code, then add a little recency — deduplicated and budget-bound.
    """
    now = datetime.now(timezone.utc)
    lines: list[str] = []
    shown: set = set()  # entry ids already printed — never repeat across sections

    project_label = project or "default"
    now_utc = now.strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"## Project Memory — {project_label}  ({now_utc})")
    lines.append("")

    active = [e for e in entries if e.get("status") == "active"]

    def _emit(section_entries: list, header: str, limit: int) -> None:
        picked = [e for e in section_entries if e.get("id") not in shown][:limit]
        if not picked:
            return
        lines.append(header)
        for e in picked:
            lines.append(_format_entry(e))
            shown.add(e.get("id"))
        lines.append("")

    # 1. Key decisions — the "why" that should never be relearned. Highest value.
    decisions = sorted(
        (e for e in active if e.get("type") == "decision"),
        key=lambda e: _eff_conf(e, now), reverse=True,
    )
    _emit(decisions, "### Key Decisions", 5)

    # 2. Most-trusted knowledge by decayed confidence (any remaining type).
    high_value = sorted(active, key=lambda e: _eff_conf(e, now), reverse=True)
    _emit(high_value, "### High-Confidence Memories", 6)

    # 3. Churn-prone surfaces — warn the agent off repeatedly reverted code.
    unstable_surfaces: dict[str, int] = {}
    for e in active:
        if "unstable" in (e.get("tags") or []):
            for s in (e.get("functions") or []) or (e.get("files") or []):
                unstable_surfaces[s] = unstable_surfaces.get(s, 0) + 1
    if unstable_surfaces:
        top = sorted(unstable_surfaces.items(), key=lambda kv: kv[1], reverse=True)[:8]
        lines.append("### ⚠ Unstable / Churn-Prone (avoid re-litigating)")
        for surface, n in top:
            lines.append(f"- {surface}  ({n} add/revert-tagged entries)")
        lines.append("")

    # 4. A little recency so brand-new context isn't lost.
    recent = sorted(active, key=lambda e: e.get("timestamp", ""), reverse=True)
    _emit(recent, "### Recent Activity", 5)

    # 5. Open conflicts.
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

    # 6. Stats footer.
    total = len(entries)
    by_type: dict[str, int] = {}
    for e in entries:
        by_type[e.get("type", "note")] = by_type.get(e.get("type", "note"), 0) + 1
    stats_parts = [f"{k}={v}" for k, v in sorted(by_type.items())]
    lines.append(f"### Stats  total={total}  active={len(active)}  " + "  ".join(stats_parts))
    lines.append("\nTip: call memory_query before editing to recall relevant fixes; "
                 "memory_confirm/memory_reject to keep confidence accurate.")

    return "\n".join(lines)


def main() -> None:
    # The hook output (and memory content) is UTF-8; Windows consoles default to
    # cp1252 and would crash on characters like → / ⚠ / ↔. Force UTF-8.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    # CLI flags let non-VS-Code hosts call the injector directly:
    #   --project <slug>          explicit project (skips stdin detection)
    #   --format plain|json       plain = raw markdown on stdout (Claude Code
    #                             SessionStart hooks add stdout to context);
    #                             json  = VS Code hook protocol (default)
    argv = sys.argv[1:]
    cli_project: str | None = None
    out_format = "json"
    i = 0
    while i < len(argv):
        if argv[i] == "--project" and i + 1 < len(argv):
            cli_project = argv[i + 1]
            i += 2
        elif argv[i] == "--format" and i + 1 < len(argv):
            out_format = argv[i + 1].strip().lower()
            i += 2
        else:
            i += 1

    data = {}
    if cli_project is None and not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
            data = json.loads(raw) if raw.strip() else {}
        except (json.JSONDecodeError, OSError):
            data = {}

    project = cli_project or _detect_project_from_session(data)

    # Resolve data directory
    if project:
        slug = project.strip().lower().replace(" ", "_")
        data_dir = DATA_DIR / "projects" / slug
    else:
        data_dir = DATA_DIR

    entries, conflicts = _load_data(data_dir)

    if not entries and not conflicts:
        # Nothing stored yet — don't clutter the context
        if out_format == "json":
            print(json.dumps({"continue": True}))
        sys.exit(0)

    summary = _build_summary(entries, conflicts, project)

    # Truncate if too long
    if len(summary) > MAX_CHARS:
        summary = summary[:MAX_CHARS] + "\n\n... (truncated)"

    if out_format == "plain":
        print(summary)
        sys.exit(0)

    result = {
        "continue": True,
        "systemMessage": summary,
    }
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
