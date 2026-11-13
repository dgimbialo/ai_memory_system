#!/usr/bin/env python3
"""AI Memory System — one-command connector for AI IDEs.

Supported IDEs
--------------
• Claude Code  → <project>/.mcp.json
• Cursor       → <project>/.cursor/mcp.json
• VS Code      → <project>/.vscode/mcp.json  (+ Copilot instructions/hooks)
• Visual Studio 2022 (17.13+) / VS 2026 (18.x)
    – project-level  → <project>/.mcp.json       (shared with Claude Code)
    – global user    → %APPDATA%\\Microsoft\\VisualStudio\\globalMcpServers.json
    – .github/copilot-instructions.md for Copilot Chat context

Usage
-----
    cd C:\\Path\\To\\YourProject
    python C:\\ai_memory_system\\connect.py

    python connect.py --ide vs          # VS only
    python connect.py --ide claude vs   # multiple, explicit

All config writes are *merges*: existing servers/settings are preserved.
Idempotent — safe to re-run.

The written configs contain absolute paths of this machine. In a git repo they
are added to .git/info/exclude (local, never committed) unless already ignored;
pass --no-git-exclude to skip that.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent
MCP_SERVER = ROOT / "mcp_server.py"
SERVER_KEY = "ai-memory"

# Windows consoles default to cp1252 — force UTF-8 so the report (→, ✅) prints.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass


# ── helpers ───────────────────────────────────────────────────────────────────

def _slug(name: str) -> str:
    return name.strip().lower().replace(" ", "_") or "default"


def _python_exe() -> str:
    """Prefer the repo venv interpreter; fall back to the current one."""
    candidates = [
        ROOT / ".venv" / "Scripts" / "python.exe",   # Windows
        ROOT / ".venv" / "bin" / "python",            # POSIX
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return sys.executable or "python"


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        # Don't clobber a file we can't parse — back it up first.
        try:
            path.rename(path.with_suffix(path.suffix + ".bak"))
        except OSError:
            pass
        return {}


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _server_block(python_exe: str, slug: str, *, vscode_style: bool) -> Dict[str, Any]:
    """MCP server definition. VS Code requires an explicit "type": "stdio"."""
    block: Dict[str, Any] = {
        "command": python_exe,
        "args": [str(MCP_SERVER)],
        "env": {"AI_MEMORY_PROJECT": slug},
    }
    if vscode_style:
        block = {"type": "stdio", **block}
    return block


# ── per-IDE config writers (return human-readable status line) ────────────────

def _connect_claude_code(project: Path, python_exe: str, slug: str) -> List[str]:
    results: List[str] = []

    path = project / ".mcp.json"
    cfg = _load_json(path)
    servers = cfg.setdefault("mcpServers", {})
    servers[SERVER_KEY] = _server_block(python_exe, slug, vscode_style=False)
    _write_json(path, cfg)
    results.append(f"Claude Code  → {path}")

    # SessionStart hook: inject the memory summary into every new Claude Code
    # session automatically. This is the read half of the loop — without it
    # agents write memories but never recall them (audited: 8 reads vs ~390
    # writes across all projects before this hook existed).
    settings_path = project / ".claude" / "settings.json"
    settings = _load_json(settings_path)
    injector = ROOT / "core" / "context_injector.py"
    hook_cmd = f'"{python_exe}" "{injector}" --project {slug} --format plain'

    hooks = settings.setdefault("hooks", {})

    def _ensure_hook(event: str, cmd: str) -> bool:
        groups = hooks.setdefault(event, [])
        present = any(
            h.get("command") == cmd
            for grp in groups if isinstance(grp, dict)
            for h in grp.get("hooks", []) if isinstance(h, dict)
        )
        if not present:
            groups.append({"hooks": [{"type": "command", "command": cmd}]})
        return not present

    added_session = _ensure_hook("SessionStart", hook_cmd)

    # Per-prompt recall: surfaces the 1-3 memories relevant to each prompt.
    # Lightweight keyword scorer (no model load) — safe to run on every prompt.
    recall = ROOT / "core" / "prompt_recall.py"
    recall_cmd = f'"{python_exe}" "{recall}" --project {slug}'
    added_recall = _ensure_hook("UserPromptSubmit", recall_cmd)

    # End-of-session digest: the hand-off note for the next session.
    session_end = ROOT / "core" / "session_end.py"
    end_cmd = f'"{python_exe}" "{session_end}" --project {slug}'
    added_end = _ensure_hook("SessionEnd", end_cmd)

    if added_session or added_recall or added_end:
        _write_json(settings_path, settings)
        parts = []
        if added_session:
            parts.append("SessionStart injection")
        if added_recall:
            parts.append("per-prompt recall")
        if added_end:
            parts.append("session digest")
        results.append(f"Claude hook  → {settings_path}  (added: {', '.join(parts)})")
    else:
        results.append(f"Claude hook  → {settings_path}  (already configured)")

    return results


def _connect_cursor(project: Path, python_exe: str, slug: str) -> str:
    path = project / ".cursor" / "mcp.json"
    cfg = _load_json(path)
    servers = cfg.setdefault("mcpServers", {})
    servers[SERVER_KEY] = _server_block(python_exe, slug, vscode_style=False)
    _write_json(path, cfg)
    return f"Cursor       → {path}"


def _connect_vscode(project: Path, python_exe: str, slug: str) -> str:
    path = project / ".vscode" / "mcp.json"
    cfg = _load_json(path)
    servers = cfg.setdefault("servers", {})
    servers[SERVER_KEY] = _server_block(python_exe, slug, vscode_style=True)
    _write_json(path, cfg)
    return f"VS Code      → {path}"


def _connect_vs(project: Path, python_exe: str, slug: str) -> List[str]:
    """Wire ai-memory into Visual Studio 2022 (17.13+) and VS 2026 (18.x).

    Two targets:
    1. Project-level .mcp.json (same file Claude Code uses — VS reads it too).
    2. Global user-level globalMcpServers.json so every VS solution on this
       machine gets the server automatically.
    3. .github/copilot-instructions.md for Copilot Chat context (if absent).

    VS 2022 17.13+ format: {"servers": {"name": {type,command,args,env}}}
    VS 2026 global file:   same schema, lives in %APPDATA%\\Microsoft\\VisualStudio\\
    """
    results: List[str] = []
    block = _server_block(python_exe, slug, vscode_style=True)  # VS uses "type":"stdio"

    # 1. Project .mcp.json (VS 2022/2026 use this file at solution root)
    proj_mcp = project / ".mcp.json"
    cfg = _load_json(proj_mcp)
    # VS uses "servers" key; Claude Code uses "mcpServers" — keep both so the
    # same file works for both IDEs without conflict.
    cfg.setdefault("mcpServers", {})[SERVER_KEY] = _server_block(python_exe, slug, vscode_style=False)
    cfg.setdefault("servers", {})[SERVER_KEY] = block
    _write_json(proj_mcp, cfg)
    results.append(f"VS project   → {proj_mcp}")

    # 2. Global VS user config — applies to every solution on this machine
    appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    global_path = appdata / "Microsoft" / "VisualStudio" / "globalMcpServers.json"
    gcfg = _load_json(global_path)
    # The global entry serves EVERY solution, so it must not pin a project:
    # the server resolves the store from its working directory. (It used to pin
    # whichever project ran connect last - all other solutions wrote there.)
    gcfg.setdefault("servers", {})[SERVER_KEY] = {**block, "env": {}}
    _write_json(global_path, gcfg)
    results.append(f"VS global    → {global_path}")

    # 3. Copilot instructions (.github/copilot-instructions.md)
    instructions = project / ".github" / "copilot-instructions.md"
    if not instructions.exists():
        instructions.parent.mkdir(parents=True, exist_ok=True)
        instructions.write_text(_vs_copilot_instructions(slug), encoding="utf-8")
        results.append(f"VS Copilot   → {instructions}  (created)")
    else:
        # Append the memory block if not already present
        existing = instructions.read_text(encoding="utf-8")
        if "ai-memory" not in existing.lower() and "memory_query" not in existing:
            instructions.write_text(existing.rstrip() + "\n\n" + _vs_memory_block(slug),
                                    encoding="utf-8")
            results.append(f"VS Copilot   → {instructions}  (appended memory block)")
        else:
            results.append(f"VS Copilot   → {instructions}  (already configured)")

    return results


def _vs_memory_block(slug: str) -> str:
    return f"""\
## AI Memory System (ai-memory MCP)

This project has a persistent memory store (`{slug}`).
**Before modifying any code**, call the `memory_query` tool with a description
of what you are about to change — this surfaces past bug fixes, design decisions,
and churn-prone functions so you avoid re-introducing reverted code.

After completing a fix or decision, call `memory_add` to record it.

Key tools: `memory_query`, `memory_add`, `memory_confirm`, `memory_reject`,
`memory_recent`, `memory_conflicts`, `memory_stats`.
"""


def _vs_copilot_instructions(slug: str) -> str:
    return f"""\
# GitHub Copilot Instructions

## Project memory

This project has an AI memory store managed by the `ai-memory` MCP server.
{_vs_memory_block(slug)}
"""


# ── IDE detection ──────────────────────────────────────────────────────────────

def _home() -> Path:
    return Path(os.path.expanduser("~"))


def _detect_ides(project: Path) -> Dict[str, bool]:
    """Best-effort detection. We err on the side of configuring: an unused MCP
    config file is harmless, a missing one means no memory. So a target is
    'present' if either a global install marker OR a project marker exists."""
    home = _home()
    appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))

    claude = (
        (home / ".claude").exists()
        or (home / ".claude.json").exists()
        or (project / ".mcp.json").exists()
    )
    cursor = (
        (home / ".cursor").exists()
        or (local / "Programs" / "cursor").exists()
        or (appdata / "Cursor").exists()
        or (project / ".cursor").exists()
    )
    vscode = (
        (home / ".vscode").exists()
        or (appdata / "Code").exists()
        or (project / ".vscode").exists()
    )
    # Visual Studio 2022 (17.x) or VS 2026 (18.x) — detect by VS AppData dirs
    vs_dir = local / "Microsoft" / "VisualStudio"
    vs = any(
        d.name.startswith(("17.", "18.")) and d.is_dir()
        for d in vs_dir.iterdir()
    ) if vs_dir.exists() else False
    return {"claude": claude, "cursor": cursor, "vscode": vscode, "vs": vs}


# ── store initialisation ───────────────────────────────────────────────────────

def _init_store(slug: str) -> Tuple[Path, int]:
    """Ensure data/projects/<slug>/ exists and return (data_dir, entry_count)."""
    sys.path.insert(0, str(ROOT))
    from core.engine import MemoryEngine

    data_dir = ROOT / "data" / "projects" / slug
    engine = MemoryEngine(data_dir)  # _ensure_initialized() creates empty stores
    try:
        count = len(engine.list_memory())
    except Exception:
        count = 0
    return data_dir, count


def _setup_copilot_hooks(project: Path, slug: str) -> str:
    """Reuse the existing VS Code/Copilot infra scaffolder (instructions+hooks)."""
    try:
        sys.path.insert(0, str(ROOT))
        from core.vscode_infra import VSCodeInfraBuilder

        builder = VSCodeInfraBuilder(ROOT / "data")
        builder.create_in(str(project), project_name=project.name)
        return "Copilot      → .github/copilot-instructions.md + .vscode/settings.json"
    except Exception as exc:  # never fail the whole connect over Copilot extras
        return f"Copilot      → skipped ({exc})"


# ── keep machine-local configs out of git ──────────────────────────────────────

# Files connect.py may write. Any of them that embeds the absolute path of THIS
# machine's memory system must not be published: a committed .mcp.json once let
# a scraper find the author through a public repo.
_LOCAL_CONFIG_FILES = (
    ".mcp.json",
    ".claude/settings.json",
    ".cursor/mcp.json",
    ".vscode/mcp.json",
    ".vscode/settings.json",
)
_EXCLUDE_HEADER = "# AI Memory System: machine-local configs (added by connect.py)"


def _git(project: Path, *args: str) -> Tuple[int, str]:
    """Run git in the project; (returncode, stdout). (1, "") when git is absent."""
    kwargs: Dict[str, Any] = dict(capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=10)
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000  # no console window
    try:
        r = subprocess.run(["git", "-C", str(project), *args], **kwargs)
        return r.returncode, r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def _has_local_paths(path: Path) -> bool:
    """True when the file mentions this machine's memory-system folder."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return False
    root = str(ROOT).lower()
    needles = {root, root.replace("\\", "\\\\"), root.replace("\\", "/")}
    return any(n in text for n in needles)


def _exclude_pattern(rel: str) -> str:
    """Anchored .git/info/exclude pattern for a repo-relative posix path."""
    escaped = "".join("\\" + ch if ch in "\\*?[" else ch for ch in rel)
    return "/" + escaped


def _git_exclude_local_configs(project: Path) -> List[str]:
    """Hide machine-local config files from git via the repo's own
    .git/info/exclude (never committed, so nothing changes for collaborators).

    Only files that exist, contain a path of this machine, are not tracked and
    are not already ignored get a line. A tracked file cannot be hidden this
    way, so it is reported instead of being touched. Returns report lines.
    """
    rc, top = _git(project, "rev-parse", "--show-toplevel")
    if rc != 0 or not top:
        return []  # not a git repo (or git missing): nothing to protect
    rc, exclude_raw = _git(project, "rev-parse", "--git-path", "info/exclude")
    if rc != 0 or not exclude_raw:
        return []
    toplevel = Path(top).resolve()
    exclude_path = Path(exclude_raw)
    if not exclude_path.is_absolute():
        exclude_path = project / exclude_path

    to_add: List[str] = []
    added_names: List[str] = []
    tracked: List[str] = []
    for name in _LOCAL_CONFIG_FILES:
        path = project / name
        if not path.is_file() or not _has_local_paths(path):
            continue
        try:
            rel = path.resolve().relative_to(toplevel).as_posix()
        except ValueError:
            continue
        if _git(project, "ls-files", "--error-unmatch", "--", name)[0] == 0:
            tracked.append(rel)
            continue
        if _git(project, "check-ignore", "-q", "--", name)[0] == 0:
            continue  # already ignored by .gitignore / exclude / global ignore
        to_add.append(_exclude_pattern(rel))
        added_names.append(rel)

    results: List[str] = []
    if to_add:
        try:
            existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
            chunk = "" if not existing or existing.endswith("\n") else "\n"
            if _EXCLUDE_HEADER not in existing:
                chunk += _EXCLUDE_HEADER + "\n"
            chunk += "\n".join(to_add) + "\n"
            exclude_path.parent.mkdir(parents=True, exist_ok=True)
            with exclude_path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(chunk)
            results.append(f"Git exclude  → {exclude_path}  (hidden from git: {', '.join(added_names)})")
        except OSError as exc:
            results.append(f"Git exclude  → FAILED to write {exclude_path} ({exc}); "
                           f"do not commit: {', '.join(added_names)}")
    for rel in tracked:
        results.append(f"Git WARNING  → {rel} is tracked by git and contains local paths of this "
                       f"machine (stop publishing it with: git rm --cached \"{rel}\")")
    return results


# ── connected-projects registry ────────────────────────────────────────────────

REGISTRY_FILE = ROOT / "data" / "connected_projects.json"


def _register_project(slug: str, project: Path) -> None:
    """Remember where each connected project lives so `--all` can refresh
    every one of them after the memory system gains new integration pieces.

    Audit #2 finding: the SessionStart hook shipped in July was only ever
    installed in the one project where connect.py happened to be re-run —
    every other project kept writing memories nobody read.
    """
    from datetime import datetime, timezone
    reg = _load_json(REGISTRY_FILE)
    reg[slug] = {
        "path": str(project),
        "last_connected": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(REGISTRY_FILE, reg)


def connect_all(*, ides: List[str], with_copilot: bool,
                git_exclude: bool = True) -> List[Dict[str, Any]]:
    """Re-run connect() for every registered project that still exists."""
    reg = _load_json(REGISTRY_FILE)
    results: List[Dict[str, Any]] = []
    for slug, info in sorted(reg.items()):
        if info.get("auto"):
            # Auto-registered by the resolver (path -> store mapping only): the
            # user never asked for config files to be written into that folder.
            continue
        path = Path(info.get("path", ""))
        if not path.exists():
            print(f"  (skip {slug}: path gone — {path})")
            continue
        results.append(connect(path, ides=ides, with_copilot=with_copilot,
                               git_exclude=git_exclude))
    return results


# ── main ───────────────────────────────────────────────────────────────────────

def connect(project: Path, *, ides: List[str], with_copilot: bool,
            git_exclude: bool = True) -> Dict[str, Any]:
    project = project.resolve()
    slug = _slug(project.name)
    python_exe = _python_exe()

    data_dir, count = _init_store(slug)

    detected = _detect_ides(project)
    targets = set(ides) if ides else {k for k, v in detected.items() if v}
    if not targets:  # nothing detected and nothing forced — configure all four
        targets = {"claude", "cursor", "vscode", "vs"}

    actions: List[str] = []
    if "claude" in targets:
        actions.extend(_connect_claude_code(project, python_exe, slug))
    if "cursor" in targets:
        actions.append(_connect_cursor(project, python_exe, slug))
    if "vscode" in targets:
        actions.append(_connect_vscode(project, python_exe, slug))
        if with_copilot:
            actions.append(_setup_copilot_hooks(project, slug))
    if "vs" in targets:
        actions.extend(_connect_vs(project, python_exe, slug))

    if git_exclude:
        try:
            actions.extend(_git_exclude_local_configs(project))
        except Exception as exc:  # protection is best-effort, never fail the connect
            actions.append(f"Git exclude  → skipped ({exc})")

    _register_project(slug, project)

    return {
        "project": str(project),
        "slug": slug,
        "python": python_exe,
        "data_dir": str(data_dir),
        "existing_entries": count,
        "detected": detected,
        "configured": sorted(targets),
        "actions": actions,
    }


def _print_report(r: Dict[str, Any]) -> None:
    print("\n  AI Memory — connected ✅\n")
    print(f"  Project : {r['project']}")
    print(f"  Store   : {r['data_dir']}  ({r['existing_entries']} entries)")
    print(f"  Python  : {r['python']}")
    det = r["detected"]
    print(f"  Detected: " + ", ".join(k for k, v in det.items() if v) or "(none — configured all)")
    print("\n  Wired up:")
    for a in r["actions"]:
        print(f"    • {a}")
    print(
        "\n  Next step: reload / restart your IDE so it picks up the new MCP server.\n"
        "  Then ask the agent to \"check project memory\" — it now has memory_query,\n"
        "  memory_add, memory_recent, memory_conflicts and memory_stats tools.\n"
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="connect",
        description="One-command memory integration for Claude Code, Cursor and VS Code.",
    )
    p.add_argument("path", nargs="?", default=".", help="Project folder (default: current dir).")
    p.add_argument(
        "--ide", action="append", choices=["claude", "cursor", "vscode", "vs"], default=[],
        help="Force a specific IDE (repeatable). Default: auto-detect.",
    )
    p.add_argument("--all", action="store_true",
                   help="Re-connect every previously registered project (refresh hooks/configs).")
    p.add_argument("--no-copilot", action="store_true", help="Skip VS Code/Copilot hook scaffolding.")
    p.add_argument("--no-git-exclude", action="store_true",
                   help="Do not add the machine-local config files to .git/info/exclude.")
    p.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of a report.")
    return p


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.all:
        results = connect_all(ides=args.ide, with_copilot=not args.no_copilot,
                              git_exclude=not args.no_git_exclude)
        if args.json:
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            for r in results:
                _print_report(r)
            print(f"  Refreshed {len(results)} project(s) from the registry.")
        return 0

    project = Path(args.path)
    if not project.exists():
        print(f"Error: project path does not exist: {project}", file=sys.stderr)
        return 1
    result = connect(project, ides=args.ide, with_copilot=not args.no_copilot,
                     git_exclude=not args.no_git_exclude)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        _print_report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
