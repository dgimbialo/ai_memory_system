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
"""
from __future__ import annotations

import argparse
import json
import os
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
    session_start = hooks.setdefault("SessionStart", [])
    already = any(
        h.get("command") == hook_cmd
        for grp in session_start if isinstance(grp, dict)
        for h in grp.get("hooks", []) if isinstance(h, dict)
    )
    if not already:
        session_start.append({"hooks": [{"type": "command", "command": hook_cmd}]})
        _write_json(settings_path, settings)
        results.append(f"Claude hook  → {settings_path}  (SessionStart memory injection)")
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
    gcfg.setdefault("servers", {})[SERVER_KEY] = {
        **block,
        # For global config the slug must come from the workspace; use env fallback.
        "env": {"AI_MEMORY_PROJECT": slug, "AI_MEMORY_DATA_DIR": ""},
    }
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


# ── main ───────────────────────────────────────────────────────────────────────

def connect(project: Path, *, ides: List[str], with_copilot: bool) -> Dict[str, Any]:
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
    p.add_argument("--no-copilot", action="store_true", help="Skip VS Code/Copilot hook scaffolding.")
    p.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of a report.")
    return p


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project = Path(args.path)
    if not project.exists():
        print(f"Error: project path does not exist: {project}", file=sys.stderr)
        return 1
    result = connect(project, ides=args.ide, with_copilot=not args.no_copilot)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        _print_report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
