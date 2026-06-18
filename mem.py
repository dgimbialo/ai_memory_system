#!/usr/bin/env python3
"""Universal memory CLI wrapper for Claude Code slash commands.

All /mem* slash commands delegate here. The wrapper resolves the project slug
from the current working directory so no --project flag is required in the
slash command invocation.

Usage (called by slash commands, not directly):
    python mem.py query "grace note pairing"
    python mem.py add bug_fix "fix desc" [--fix "..."] [--files "a.cpp,b.cpp"]
    python mem.py recent [--n 10]
    python mem.py conflicts
    python mem.py stats
    python mem.py session
    python mem.py list [--type bug_fix] [--status active]
    python mem.py confirm <id>
    python mem.py reject <id>
    python mem.py decay [--apply]
    python mem.py dedup [--apply]
    python mem.py recompute [--apply]
    python mem.py lint
    python mem.py wiki
    python mem.py ui [--port 5001]

Project resolution order (same as mcp_server.py):
  1. $AI_MEMORY_PROJECT env (explicit override)
  2. $AI_MEMORY_DATA_DIR env (absolute data path)
  3. cwd name slug matched against data/projects/
  4. parent dirs (up to 3 levels) name slug
  5. default store
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
if not VENV_PY.exists():
    VENV_PY = ROOT / ".venv" / "bin" / "python"
PYTHON = str(VENV_PY) if VENV_PY.exists() else sys.executable
RUN = str(ROOT / "run.py")

# Force UTF-8 on Windows
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore
    except (AttributeError, ValueError):
        pass


# ── project resolution ────────────────────────────────────────────────────────

def _slug(name: str) -> str:
    return name.strip().lower().replace(" ", "_") or "default"


def _resolve_project() -> Optional[str]:
    """Return a project slug or path that run.py understands, or None."""
    # 1. Explicit env override
    explicit = os.environ.get("AI_MEMORY_PROJECT")
    if explicit:
        return explicit
    if os.environ.get("AI_MEMORY_DATA_DIR"):
        return None  # engine uses env directly; don't pass --project

    # 2. cwd and its parents
    projects_dir = ROOT / "data" / "projects"
    check = [Path.cwd()] + list(Path.cwd().parents)[:3]
    for p in check:
        slug = _slug(p.name)
        if (projects_dir / slug).exists():
            return slug

    return None  # fall through to default store


def _base_cmd(project: Optional[str] = None) -> List[str]:
    cmd = [PYTHON, RUN]
    p = project if project is not None else _resolve_project()
    if p:
        cmd += ["--project", p]
    return cmd


def _run(args: List[str], project: Optional[str] = None) -> int:
    cmd = _base_cmd(project) + args
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    result = subprocess.run(cmd, text=True, encoding="utf-8", env=env)
    return result.returncode


def _run_json(args: List[str], project: Optional[str] = None):
    cmd = _base_cmd(project) + args
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)
    if r.returncode != 0:
        print(r.stderr or r.stdout, file=sys.stderr)
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        print(r.stdout)
        return None


# ── formatters ────────────────────────────────────────────────────────────────

def _fmt_query(results) -> None:
    if not results:
        print("No matching memories found.")
        return
    for r in results:
        unstable = "  ⚠ UNSTABLE" if "unstable" in r.get("tags", []) else ""
        conf = r.get("score", 0)
        print(f"[{r.get('id','?')[:8]}] ({r.get('type')}, score={conf:.2f}){unstable}")
        print(f"  {r.get('description','')}")
        if r.get("fix_summary"):
            print(f"  fix: {r['fix_summary'][:200]}")
        if r.get("files"):
            print(f"  files: {', '.join(r['files'])}")
        tags = [t for t in r.get("tags", []) if not t.startswith("project:")]
        if tags:
            print(f"  tags: {', '.join(tags)}")
        print()


def _fmt_entry(e: dict) -> None:
    conf = e.get("confidence", 0)
    unstable = "  ⚠ UNSTABLE" if "unstable" in e.get("tags", []) else ""
    print(f"[{e.get('id','?')[:8]}] ({e.get('type')}, conf={conf:.2f}, {e.get('status')}){unstable}")
    print(f"  {e.get('description','')}")
    if e.get("files"):
        print(f"  files: {', '.join(e.get('files', []))}")


# ── sub-commands ──────────────────────────────────────────────────────────────

def cmd_query(args):
    q = " ".join(args.query) if args.query else ""
    if not q:
        print("Usage: /memq <your question or context>"); return 1
    d = _run_json(["query_memory", q, "--top-k", str(args.n), "--format", "concise"])
    if d is not None:
        _fmt_query(d)
    return 0


def cmd_add(args):
    extra = []
    if args.fix:      extra += ["--fix", args.fix]
    if args.cause:    extra += ["--cause", args.cause]
    if args.files:    extra += ["--files"] + args.files.split(",")
    if args.functions: extra += ["--functions"] + args.functions.split(",")
    if args.tags:     extra += ["--tags"] + args.tags.split(",")
    r = _run_json(["add_memory", "--type", args.type,
                   "--description", " ".join(args.description)] + extra)
    if r:
        e = r.get("entry", {})
        print(f"✅ Saved [{e.get('id','?')[:8]}] ({e.get('type')}) — {e.get('description','')[:80]}")
        if r.get("revert_warning"):
            print("⚠  " + r["revert_warning"].get("message", "Revert pattern detected"))
    return 0


def cmd_recent(args):
    entries = _run_json(["list_memory"])
    if entries:
        active = [e for e in entries if e.get("status") == "active"]
        active.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
        for e in active[:args.n]:
            _fmt_entry(e)
    return 0


def cmd_conflicts(args):
    return _run(["list_conflicts"])


def cmd_stats(args):
    return _run(["state"])


def cmd_session(args):
    extra = []
    if args.context: extra += ["--context", args.context]
    return _run(["session_summary"] + extra)


def cmd_list(args):
    extra = []
    if args.type:   extra += ["--type", args.type]
    if args.status: extra += ["--status", args.status]
    if args.file:   extra += ["--file", args.file]
    return _run(["list_memory"] + extra)


def cmd_confirm(args):
    r = _run_json(["reinforce", args.id])
    if r:
        print(f"✅ Reinforced [{args.id[:8]}]: conf {r['old']} → {r['new']} (used {r['usage_count']}×)")
    return 0


def cmd_reject(args):
    r = _run_json(["weaken", args.id])
    if r:
        print(f"↓ Weakened [{args.id[:8]}]: conf {r['old']} → {r['new']}")
    return 0


def cmd_decay(args):
    flags = ["--apply"] if args.apply else ["--dry-run"]
    return _run(["decay"] + flags)


def cmd_dedup(args):
    flags = ["--apply"] if args.apply else ["--dry-run"]
    return _run(["deduplicate"] + flags)


def cmd_recompute(args):
    flags = ["--apply"] if args.apply else ["--dry-run"]
    return _run(["recompute_unstable"] + flags)


def cmd_lint(args):
    return _run(["lint"])


def cmd_wiki(args):
    return _run(["render_wiki"])


def cmd_ui(args):
    """Start the local web dashboard and open it in a browser."""
    import threading, time
    p = str(args.port)
    proj = _resolve_project()
    server_args = [PYTHON, str(ROOT / "server.py"), "--port", p, "--no-browser"]
    if proj:
        server_args += ["--project", proj]
    print(f"Starting memory dashboard on http://127.0.0.1:{p} …")
    proc = subprocess.Popen(server_args)
    time.sleep(1.2)
    webbrowser.open(f"http://127.0.0.1:{p}")
    print("Press Ctrl+C to stop the server.")
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
    return 0


# ── parser ────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mem", add_help=True)
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("query", help="Semantic search")
    q.add_argument("query", nargs="*")
    q.add_argument("--n", type=int, default=8)
    q.set_defaults(func=cmd_query)

    a = sub.add_parser("add", help="Add a memory entry")
    a.add_argument("type", choices=["bug_fix","feature","decision","note"])
    a.add_argument("description", nargs="+")
    a.add_argument("--fix", default=None)
    a.add_argument("--cause", default=None)
    a.add_argument("--files", default=None, help="comma-separated")
    a.add_argument("--functions", default=None, help="comma-separated")
    a.add_argument("--tags", default=None, help="comma-separated")
    a.set_defaults(func=cmd_add)

    r = sub.add_parser("recent", help="Recent entries")
    r.add_argument("--n", type=int, default=12)
    r.set_defaults(func=cmd_recent)

    sub.add_parser("conflicts", help="List unresolved conflicts").set_defaults(func=cmd_conflicts)
    sub.add_parser("stats", help="Store stats / health").set_defaults(func=cmd_stats)

    ss = sub.add_parser("session", help="Create a session summary entry")
    ss.add_argument("--context", default=None)
    ss.set_defaults(func=cmd_session)

    ls = sub.add_parser("list", help="List / filter entries")
    ls.add_argument("--type", default=None)
    ls.add_argument("--status", default=None)
    ls.add_argument("--file", default=None)
    ls.set_defaults(func=cmd_list)

    cf = sub.add_parser("confirm", help="Confirm (reinforce) a memory")
    cf.add_argument("id")
    cf.set_defaults(func=cmd_confirm)

    rj = sub.add_parser("reject", help="Reject (weaken) a memory")
    rj.add_argument("id")
    rj.set_defaults(func=cmd_reject)

    dc = sub.add_parser("decay", help="Apply confidence decay")
    dc.add_argument("--apply", action="store_true")
    dc.set_defaults(func=cmd_decay)

    dd = sub.add_parser("dedup", help="Find/merge duplicates")
    dd.add_argument("--apply", action="store_true")
    dd.set_defaults(func=cmd_dedup)

    rc = sub.add_parser("recompute", help="Re-evaluate unstable tags")
    rc.add_argument("--apply", action="store_true")
    rc.set_defaults(func=cmd_recompute)

    sub.add_parser("lint", help="Health-check the store").set_defaults(func=cmd_lint)
    sub.add_parser("wiki", help="Render markdown wiki").set_defaults(func=cmd_wiki)

    ui = sub.add_parser("ui", help="Start web dashboard")
    ui.add_argument("--port", type=int, default=5001)
    ui.set_defaults(func=cmd_ui)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
