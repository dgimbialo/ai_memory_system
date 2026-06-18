#!/usr/bin/env python3
"""AI Memory System — MCP server (stdio, JSON-RPC 2.0, pure stdlib).

This is the *universal integration point*. Claude Code, Cursor and VS Code
(Copilot / Continue / any MCP client) all speak the Model Context Protocol over
stdio, so a single server file makes the project memory available as native
tools inside every AI agent — no per-IDE plugin required.

The server resolves which project store to use from, in priority order:
    1. env  AI_MEMORY_DATA_DIR   — absolute path to a data dir
    2. env  AI_MEMORY_PROJECT    — project slug under <root>/data/projects/<slug>
    3. cwd basename              — slugified folder name of the client's workspace
    4. <root>/data               — the default store

Protocol notes
--------------
* stdout carries ONLY framed JSON-RPC messages. All diagnostics go to stderr.
* Implements: initialize, notifications/initialized, tools/list, tools/call,
  ping. Unknown methods return a JSON-RPC "method not found" error.
* Tool results follow the MCP shape: {"content": [{"type": "text", ...}]}.

Run manually for a smoke test:
    echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python mcp_server.py
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Force UTF-8 on the stdio channel — Windows consoles default to cp1252, which
# would crash on non-ASCII memory content (and on framing characters like ⚠).
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "ai-memory"
SERVER_VERSION = "1.0.0"


# ── diagnostics (stderr only — never pollute the stdio channel) ───────────────

def _log(msg: str) -> None:
    sys.stderr.write(f"[mcp_server] {msg}\n")
    sys.stderr.flush()


# ── project / engine resolution ───────────────────────────────────────────────

def _slug(name: str) -> str:
    return name.strip().lower().replace(" ", "_") or "default"


def _resolve_data_dir() -> Path:
    explicit = os.environ.get("AI_MEMORY_DATA_DIR")
    if explicit:
        return Path(explicit)
    project = os.environ.get("AI_MEMORY_PROJECT")
    if project:
        return ROOT / "data" / "projects" / _slug(project)
    cwd_name = Path.cwd().name
    if cwd_name:
        candidate = ROOT / "data" / "projects" / _slug(cwd_name)
        if candidate.exists():
            return candidate
    return ROOT / "data"


_ENGINE = None


def _engine():
    """Lazily build the MemoryEngine (importing core also loads embeddings)."""
    global _ENGINE
    if _ENGINE is None:
        from core.engine import MemoryEngine

        data_dir = _resolve_data_dir()
        _log(f"using data dir: {data_dir}")
        _ENGINE = MemoryEngine(data_dir)
    return _ENGINE


# ── tool definitions ──────────────────────────────────────────────────────────

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "memory_query",
        "description": (
            "Semantic search over this project's accumulated memory (past bug "
            "fixes, decisions, features, gotchas). Call this BEFORE writing or "
            "changing code to recall how a file/feature was handled before and "
            "to avoid re-introducing reverted approaches."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language description of what you are about to work on."},
                "top_k": {"type": "integer", "description": "How many results (default 5).", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_add",
        "description": (
            "Record a new memory entry after you fix a bug, make a design "
            "decision, or add a feature. Persists so future sessions remember it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["bug_fix", "feature", "decision", "note"]},
                "description": {"type": "string", "description": "One-line summary of what happened."},
                "cause": {"type": "string", "description": "Root cause (for bug_fix) or motivation."},
                "fix": {"type": "string", "description": "What was changed / decided."},
                "files": {"type": "array", "items": {"type": "string"}, "description": "Files touched (repo-relative)."},
                "functions": {"type": "array", "items": {"type": "string"}, "description": "Functions/symbols touched."},
                "tags": {"type": "array", "items": {"type": "string"}},
                "decisions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["type", "description"],
        },
    },
    {
        "name": "memory_confirm",
        "description": (
            "Confirm a memory was correct / useful (raises its confidence and "
            "keeps it fresh). Call after a recalled entry helped you and proved "
            "right. Pass the entry id from a prior memory_query result."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    },
    {
        "name": "memory_reject",
        "description": (
            "Mark a memory as wrong / outdated (lowers its confidence). Call "
            "when a recalled entry misled you or no longer holds."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    },
    {
        "name": "memory_recent",
        "description": "List the most recent active memory entries for this project.",
        "inputSchema": {
            "type": "object",
            "properties": {"n": {"type": "integer", "default": 10}},
        },
    },
    {
        "name": "memory_conflicts",
        "description": "List unresolved conflicts (contradictory/duplicate memories) for this project.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "memory_stats",
        "description": "Summary of the project's memory store: counts by type/status, conflicts, store health.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# ── tool implementations ──────────────────────────────────────────────────────

def _tool_memory_query(args: Dict[str, Any]) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "Error: 'query' is required."
    top_k = int(args.get("top_k", 5) or 5)
    eng = _engine()
    results = eng.query_memory(query, top_k=top_k, fmt="concise")
    if not results:
        return "No matching memories found."
    # Recall resets the decay clock for surfaced entries (freshness signal).
    try:
        eng.touch_used([r["id"] for r in results])
    except Exception:
        pass
    lines = [f"Top {len(results)} memories for: {query!r}\n"]
    for r in results:
        tags = ", ".join(r.get("tags", []))
        unstable = "  ⚠ UNSTABLE (repeatedly reverted)" if "unstable" in r.get("tags", []) else ""
        lines.append(
            f"• [{r['score']}] ({r.get('type')}, {r.get('status')}) {r.get('description')}{unstable}\n"
            f"    fix: {r.get('fix_summary', '')}\n"
            f"    files: {', '.join(r.get('files', []))}"
            + (f"\n    tags: {tags}" if tags else "")
        )
    return "\n".join(lines)


def _tool_memory_add(args: Dict[str, Any]) -> str:
    payload = {
        "type": args.get("type", "note"),
        "description": args.get("description", ""),
        "cause": args.get("cause", ""),
        "fix": args.get("fix", ""),
        "files": args.get("files", []) or [],
        "functions": args.get("functions", []) or [],
        "tags": (args.get("tags", []) or []) + ["mcp"],
        "decisions": args.get("decisions", []) or [],
        "status": "active",
    }
    result = _engine().add_memory(payload)
    entry = result.get("entry", {})
    msg = f"✅ Saved memory {entry.get('id', '?')} ({entry.get('type')})."
    if result.get("revert_warning"):
        msg += "\n⚠ " + result["revert_warning"].get("message", "Revert pattern detected.")
    return msg


def _tool_memory_confirm(args: Dict[str, Any]) -> str:
    eid = str(args.get("id", "")).strip()
    if not eid:
        return "Error: 'id' is required."
    try:
        r = _engine().reinforce(eid, reason="confirmed via MCP")
        return f"✅ Reinforced {eid}: confidence {r['old']} → {r['new']} (used {r['usage_count']}×)."
    except KeyError:
        return f"Error: no memory with id {eid}."


def _tool_memory_reject(args: Dict[str, Any]) -> str:
    eid = str(args.get("id", "")).strip()
    if not eid:
        return "Error: 'id' is required."
    try:
        r = _engine().weaken(eid, reason="rejected via MCP")
        return f"↓ Weakened {eid}: confidence {r['old']} → {r['new']}."
    except KeyError:
        return f"Error: no memory with id {eid}."


def _tool_memory_recent(args: Dict[str, Any]) -> str:
    n = int(args.get("n", 10) or 10)
    entries = _engine().list_memory()
    active = [e for e in entries if e.get("status") == "active"]
    active.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    recent = active[:n]
    if not recent:
        return "No active memories yet."
    lines = [f"{len(recent)} most recent active memories:"]
    for e in recent:
        lines.append(
            f"• [{e.get('id', '?')[:8]}] ({e.get('type')}) {e.get('description', '')[:120]}"
            f"  → {', '.join(e.get('files', []))[:60]}"
        )
    return "\n".join(lines)


def _tool_memory_conflicts(_: Dict[str, Any]) -> str:
    conflicts = _engine().list_conflicts()
    if not conflicts:
        return "No unresolved conflicts. 🎉"
    lines = [f"{len(conflicts)} unresolved conflict(s):"]
    for c in conflicts:
        lines.append(f"• {c.get('id', '?')[:8]}: {c.get('reason', '')}")
    return "\n".join(lines)


def _tool_memory_stats(_: Dict[str, Any]) -> str:
    state = _engine().state()
    return json.dumps(state, ensure_ascii=False, indent=2)


_DISPATCH = {
    "memory_query": _tool_memory_query,
    "memory_add": _tool_memory_add,
    "memory_confirm": _tool_memory_confirm,
    "memory_reject": _tool_memory_reject,
    "memory_recent": _tool_memory_recent,
    "memory_conflicts": _tool_memory_conflicts,
    "memory_stats": _tool_memory_stats,
}


# ── JSON-RPC plumbing ─────────────────────────────────────────────────────────

def _send(message: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _result(req_id: Any, result: Dict[str, Any]) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "result": result})


def _error(req_id: Any, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def _handle(message: Dict[str, Any]) -> None:
    method = message.get("method")
    req_id = message.get("id")
    params = message.get("params") or {}

    # Notifications (no id) — never reply.
    if req_id is None:
        return

    if method == "initialize":
        _result(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
        return

    if method == "ping":
        _result(req_id, {})
        return

    if method == "tools/list":
        _result(req_id, {"tools": TOOLS})
        return

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        fn = _DISPATCH.get(name)
        if fn is None:
            _error(req_id, -32602, f"Unknown tool: {name}")
            return
        import time as _time
        _t0 = _time.perf_counter()
        try:
            text = fn(args)
            _result(req_id, {"content": [{"type": "text", "text": text}], "isError": False})
            _audit_mcp(name, args, 200, (_time.perf_counter() - _t0) * 1000)
        except Exception as exc:  # tool errors are reported in-band, not as RPC errors
            _log("tool error:\n" + traceback.format_exc())
            _result(req_id, {
                "content": [{"type": "text", "text": f"Tool '{name}' failed: {exc}"}],
                "isError": True,
            })
            _audit_mcp(name, args, 500, (_time.perf_counter() - _t0) * 1000)
        return

    _error(req_id, -32601, f"Method not found: {method}")


def _audit_mcp(tool: str, args: Dict[str, Any], status: int, duration_ms: float) -> None:
    """Write an MCP tool call to the activity_log of the active project.

    This lets the web UI show MCP calls from Cursor / VS Code / Claude Code
    alongside direct HTTP API calls in the real-time log tab.
    """
    try:
        eng = _engine()
        from datetime import datetime, timezone as _tz
        eng._log(
            action=f"mcp:{tool}",
            affected=[],
            reason=f"MCP tool call (status={status}, {duration_ms:.0f}ms) args={json.dumps(args, ensure_ascii=False, default=str)[:200]}",
        )
    except Exception:
        pass  # never break the MCP channel over logging


def main() -> int:
    _log(f"starting {SERVER_NAME} v{SERVER_VERSION} (protocol {PROTOCOL_VERSION})")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            _log(f"skipping non-JSON line: {line[:120]}")
            continue
        try:
            _handle(message)
        except Exception:
            _log("fatal handler error:\n" + traceback.format_exc())
    _log("stdin closed — exiting")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
