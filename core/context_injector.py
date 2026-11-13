"""SessionStart hook - injects project memory context into every new agent session.

Input  (stdin): hook JSON with session metadata (may be empty).
Output (stdout): VS Code protocol JSON (``systemMessage``) or, with
                 ``--format plain``, raw markdown for Claude Code.

The summary is budgeted by section, most important first:

    1. Rules (pinned)                    - standing instructions, always shown
    2. Key Decisions & Durable Knowledge - the "why" that must not be relearned
    3. High-Confidence Memories
    4. Last session                      - hand-off from the previous session
    5. Unstable / churn-prone surfaces
    6. Recent activity
    7. Open conflicts, stats, tip

Sections are filled until the character budget is reached and the *least*
important lines are dropped - the old version built everything and cut the
tail, which silently removed recent activity, conflicts and stats once a store
grew (5450 chars against a 4000 cap on a 419-entry store, audit #4).

Exit code 0 always - never block the session.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "data"

CONFLICTS_N = 3
# Maximum total characters injected (keep the context-window cost small)
MAX_CHARS = 5000
RULES_BUDGET = 2400
# An automated loop opening a session every couple of minutes (one project
# logged 2871 injections in a month) gets the compact form instead.
STORM_WINDOW_MIN = 30
STORM_MAX_INJECTIONS = 6


def _detect_project_from_session(data: dict) -> str | None:
    """Try to read project from session metadata or cwd."""
    for key in ("workspaceFolder", "workspacePath", "cwd", "projectPath"):
        val = data.get(key)
        if isinstance(val, str) and val:
            return Path(val).name
    return None


def _load_data(data_dir: Path) -> tuple[list, list]:
    """Load memory entries and conflicts from the project store."""
    from core.storage import read_store
    try:
        entries = read_store(data_dir, "memory.json", [])
        conflicts = read_store(data_dir, "conflicts.json", [])
    except Exception:
        return [], []
    return (entries if isinstance(entries, list) else [],
            conflicts if isinstance(conflicts, list) else [])


def _format_entry(e: dict, width: int = 120) -> str:
    ts = e.get("timestamp", "")[:10]
    eid = e.get("id", "?")[:8]
    etype = e.get("type", "note")
    desc = e.get("description", "")[:width]
    files = ", ".join(e.get("files") or [])[:60]
    conf = e.get("confidence", 0.0)
    status = e.get("status", "active")
    marker = " ⚠in-conflict" if status == "conflict" else ""
    line = f"- [{eid}] ({etype}, conf={conf:.2f}, {status}{marker}) {desc}"
    if files:
        line += f"  → {files}"
    if ts:
        line += f"  [{ts}]"
    return line


_SORTED_TS: list = []  # write timestamps of all entries, set by _build_summary


def _eff_conf(entry: dict, now: datetime) -> float:
    """Decayed confidence via the shared formula (activity-relative age,
    decision + usage bonuses). Falls back to raw confidence if the decay
    module isn't importable (keeps the hook dependency-free)."""
    try:
        from core.decay import entry_effective_confidence
        return entry_effective_confidence(
            entry, sorted_timestamps=_SORTED_TS or None, now=now
        )
    except Exception:
        return float(entry.get("confidence") or 0.5)


def _is_pinned(e: dict) -> bool:
    return e.get("type") == "rule" or "pinned" in (e.get("tags") or [])


def _build_summary(entries: list, conflicts: list, project: str | None,
                   budget: int = MAX_CHARS, compact: bool = False,
                   shown_ids: list | None = None) -> str:
    """Relevance-first, budget-fitted session context.

    ``shown_ids`` (when given) receives the ids of every entry rendered, so the
    per-prompt recall hook does not repeat them later in the same session.
    """
    now = datetime.now(timezone.utc)
    global _SORTED_TS
    _SORTED_TS = sorted(e.get("timestamp") or "" for e in entries)
    shown: set = set()

    project_label = project or "default"
    head = f"## Project Memory — {project_label}  ({now.strftime('%Y-%m-%d %H:%M UTC')})\n"

    # Entries in an unresolved conflict are still knowledge - shown with a
    # warning marker. Archived (floor + long-unused) and carded (covered by a
    # knowledge card) entries stay out of the session start.
    live = [e for e in entries if e.get("status") in ("active", "conflict")
            and "archived" not in (e.get("tags") or [])]
    active = [e for e in live if "carded" not in (e.get("tags") or [])
              and "session-summary" not in (e.get("tags") or [])]

    by_type: dict[str, int] = {}
    for e in entries:
        by_type[e.get("type", "note")] = by_type.get(e.get("type", "note"), 0) + 1
    foot = (f"### Stats  total={len(entries)}  active={len(live)}  "
            + "  ".join(f"{k}={v}" for k, v in sorted(by_type.items()))
            + "\n\nTip: call memory_query before editing to recall relevant fixes; "
              "memory_confirm/memory_reject to keep confidence accurate.")

    def _rank_key(e: dict):
        return (_eff_conf(e, now), e.get("last_used") or e.get("timestamp") or "")

    blocks: list[tuple[str, list[str]]] = []   # (header, lines) in priority order

    # 1. Rules: standing instructions from the user/client. Never ranked away.
    rules = sorted((e for e in live if _is_pinned(e)),
                   key=lambda e: e.get("timestamp") or "", reverse=True)
    rule_lines, used = [], 0
    for e in rules:
        line = f"- [{e.get('id', '?')[:8]}] {(e.get('description') or '')[:170]}"
        if used + len(line) > RULES_BUDGET:
            rule_lines.append(f"- (+{len(rules) - len(rule_lines)} more rules: memory_query \"rules\")")
            break
        rule_lines.append(line)
        used += len(line) + 1
        shown.add(e.get("id"))
    if rule_lines:
        blocks.append(("### Rules (always apply)", rule_lines))

    def _take(pool: list, limit: int) -> list[str]:
        out = []
        for e in pool:
            if len(out) >= limit:
                break
            if e.get("id") in shown:
                continue
            out.append(_format_entry(e))
            shown.add(e.get("id"))
        return out

    if not compact:
        # 2. Key decisions and durable knowledge - guaranteed slots.
        durable = sorted((e for e in active
                          if e.get("type") == "decision" or "durable" in (e.get("tags") or [])),
                         key=_rank_key, reverse=True)
        lines = _take(durable, 7)
        if lines:
            blocks.append(("### Key Decisions & Durable Knowledge", lines))

        # 3. Most-trusted knowledge of any remaining type.
        lines = _take(sorted(active, key=_rank_key, reverse=True), 4)
        if lines:
            blocks.append(("### High-Confidence Memories", lines))

        # 4. Hand-off: what the previous session recorded.
        summaries = sorted((e for e in live if "session-summary" in (e.get("tags") or [])),
                           key=lambda e: e.get("timestamp") or "", reverse=True)
        if summaries:
            last = summaries[0]
            body = [l for l in (last.get("fix") or "").splitlines() if l.strip()][:6]
            lines = [f"- {(last.get('description') or '')[:160]}"] + [f"  {l[:140]}" for l in body]
            blocks.append(("### Last session", lines))

        # 5. Churn-prone surfaces - warn the agent off repeatedly reverted code.
        unstable_surfaces: dict[str, int] = {}
        for e in active:
            if "unstable" in (e.get("tags") or []):
                for s in (e.get("functions") or []) or (e.get("files") or []):
                    unstable_surfaces[s] = unstable_surfaces.get(s, 0) + 1
        if unstable_surfaces:
            top = sorted(unstable_surfaces.items(), key=lambda kv: kv[1], reverse=True)[:5]
            blocks.append(("### ⚠ Unstable / Churn-Prone (avoid re-litigating)",
                           [f"- {s}  ({n} add/revert-tagged entries)" for s, n in top]))

        # 6. A little recency so brand-new context isn't lost.
        lines = _take(sorted(active, key=lambda e: e.get("timestamp", ""), reverse=True), 4)
        if lines:
            blocks.append(("### Recent Activity", lines))

        # 7. Open conflicts.
        open_conflicts = [c for c in conflicts if c.get("status") != "resolved"]
        if open_conflicts:
            lines = []
            for c in open_conflicts[:CONFLICTS_N]:
                lines.append(f"- {c.get('entry_a', '?')[:8]} ↔ {c.get('entry_b', '?')[:8]}  "
                             f"sim={c.get('similarity', 0.0):.2f}  {str(c.get('reason', ''))[:70]}")
            blocks.append((f"### Open Conflicts ({len(open_conflicts)})", lines))
    else:
        foot = ("Compact injection (many sessions in a short time). Project memory has "
                f"{len(live)} live entries - use memory_query to recall what you need.")

    # Fit to budget: header and footer always; blocks in priority order, the
    # lowest-priority lines are the ones that get dropped.
    remaining = budget - len(head) - len(foot) - 2
    parts = [head]
    for header, lines in blocks:
        cost = len(header) + 1
        kept = []
        for line in lines:
            if cost + len(line) + 1 > remaining:
                break
            kept.append(line)
            cost += len(line) + 1
        if kept:
            parts.append(header + "\n" + "\n".join(kept) + "\n")
            remaining -= cost + 1
        if remaining < 80:
            break
    parts.append(foot)
    if shown_ids is not None:
        shown_ids.extend(i for i in shown if i)
    return "\n".join(parts)


def _read_hook_input(timeout: float = 2.0) -> dict:
    """Hook JSON from stdin, without ever hanging on an open-but-silent pipe."""
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    import threading
    box: dict = {}

    def _reader() -> None:
        try:
            box["raw"] = sys.stdin.read()
        except Exception:
            box["raw"] = ""

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    t.join(timeout)
    try:
        data = json.loads(box.get("raw") or "{}")
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def main() -> None:
    # The hook output (and memory content) is UTF-8; Windows consoles default to
    # cp1252 and would crash on characters like → / ⚠ / ↔. Force UTF-8.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    # CLI flags let non-VS-Code hosts call the injector directly:
    #   --project <slug>          explicit project (skips cwd detection)
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

    data = _read_hook_input()

    project = cli_project or _detect_project_from_session(data)
    session_id = str(data.get("session_id") or "")

    if project:
        slug = project.strip().lower().replace(" ", "_")
        data_dir = DATA_DIR / "projects" / slug
    else:
        data_dir = DATA_DIR

    # Warm the daemon in the background so per-prompt recall is semantic from
    # the first prompt of this session (never blocks: spawn-and-forget).
    try:
        from core import daemon_client
        daemon_client.ensure_daemon()
    except Exception:
        pass

    entries, conflicts = _load_data(data_dir)
    if not entries and not conflicts:
        # Nothing stored yet - don't clutter the context
        if out_format == "json":
            print(json.dumps({"continue": True}))
        sys.exit(0)

    compact = False
    st = None
    try:
        from core.storage import Storage
        st = Storage(data_dir)
        since = (datetime.now(timezone.utc) - timedelta(minutes=STORM_WINDOW_MIN)).isoformat()
        compact = st.count_events("inject_context", since) >= STORM_MAX_INJECTIONS
    except Exception:
        st = None

    shown_ids: list = []
    summary = _build_summary(entries, conflicts, project, compact=compact, shown_ids=shown_ids)

    # Injection IS a read of the store - make it visible to the loop metric,
    # and remember what was shown so prompt recall does not repeat it.
    if st is not None:
        try:
            st.append_log("activity_log.json", {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": "inject_context",
                "affected": [],
                "reason": "SessionStart hook injected memory summary ({} entries considered, "
                          "project={}{})".format(len(entries), project or "default",
                                                 ", compact" if compact else ""),
            })
            st.mark_seen(session_id, shown_ids)
        except Exception:
            pass

    if out_format == "plain":
        print(summary)
        sys.exit(0)

    print(json.dumps({"continue": True, "systemMessage": summary}, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
