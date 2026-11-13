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
SERVER_VERSION = "2.0.0"


# ── diagnostics (stderr only — never pollute the stdio channel) ───────────────

def _log(msg: str) -> None:
    sys.stderr.write(f"[mcp_server] {msg}\n")
    sys.stderr.flush()


# ── project / engine resolution ───────────────────────────────────────────────

def _slug(name: str) -> str:
    return name.strip().lower().replace(" ", "_") or "default"


def _resolve_data_dir() -> Path:
    """Which store does this server instance serve?

    1. AI_MEMORY_DATA_DIR - explicit path.
    2. AI_MEMORY_PROJECT  - slug pinned by a project-level config (connect.py).
    3. The working directory, via core.project_resolve: a registered project
       that contains it, else its git root (store auto-provisioned and
       registered). Anything else is the shared root store.
    """
    explicit = os.environ.get("AI_MEMORY_DATA_DIR")
    if explicit:
        return Path(explicit)
    project = os.environ.get("AI_MEMORY_PROJECT")
    if project and _slug(project) != "default":
        return ROOT / "data" / "projects" / _slug(project)
    from core.project_resolve import resolve
    slug = resolve(Path.cwd(), allow_plain_dir=False)
    return ROOT / "data" / "projects" / slug if slug else ROOT / "data"


_ENGINE = None


def _engine():
    """Lazily build the MemoryEngine (importing core also loads embeddings)."""
    global _ENGINE
    if _ENGINE is None:
        from core.engine import MemoryEngine

        data_dir = _resolve_data_dir()
        _log(f"using data dir: {data_dir}")
        # Cache misses are encoded by the local daemon (warm models) instead of
        # loading a model into this process; falls back to local when it is down.
        try:
            from core import embeddings, daemon_client
            embeddings.use_daemon(True)
            daemon_client.ensure_daemon()
        except Exception:
            pass
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
                "type": {"type": "string", "enum": ["bug_fix", "feature", "decision", "note", "rule"],
                         "description": "Use 'rule' for a standing instruction from the user/client: it is pinned into every session and never decays."},
                "description": {"type": "string", "description": "One-line summary of what happened."},
                "cause": {"type": "string", "description": "Root cause (for bug_fix) or motivation."},
                "fix": {"type": "string", "description": "What was changed / decided."},
                "files": {"type": "array", "items": {"type": "string"}, "description": "Files touched (repo-relative)."},
                "functions": {"type": "array", "items": {"type": "string"}, "description": "Functions/symbols touched."},
                "tags": {"type": "array", "items": {"type": "string"}},
                "decisions": {"type": "array", "items": {"type": "string"}},
                "supersedes": {"type": "array", "items": {"type": "string"},
                               "description": "Ids (8-char prefixes are fine) of entries this one corrects or replaces. They are marked superseded and stop being recalled."},
                "task": {"type": "string", "description": "Ticket/task reference, e.g. '#1145' or 'SS-809'."},
                "summarizes": {"type": "array", "items": {"type": "string"},
                               "description": "For a knowledge card: ids of the entries this card consolidates (see memory_consolidate)."},
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
        "name": "memory_resolve",
        "description": (
            "Resolve an open conflict between two memories (see memory_conflicts). "
            "You have the context to judge - do not leave conflicts open. "
            "action: supersede_a (B is right), supersede_b (A is right), merge "
            "(same knowledge, combine), dismiss (not actually a conflict)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Conflict id (prefix is fine)."},
                "action": {"type": "string", "enum": ["supersede_a", "supersede_b", "merge", "dismiss"]},
                "reason": {"type": "string"},
            },
            "required": ["id", "action"],
        },
    },
    {
        "name": "memory_pin",
        "description": "Pin (or unpin) a memory: pinned entries are shown at every session start and never decay. Use for standing rules.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "pinned": {"type": "boolean", "default": True}},
            "required": ["id"],
        },
    },
    {
        "name": "memory_task",
        "description": "Timeline of everything recorded about one task/ticket (e.g. '1145' or 'SS-809'), oldest first.",
        "inputSchema": {
            "type": "object",
            "properties": {"task": {"type": "string"}},
            "required": ["task"],
        },
    },
    {
        "name": "memory_consolidate",
        "description": (
            "List clusters of entries about one file that are large enough to be distilled into a "
            "single knowledge card. For each cluster, write the card with memory_add(type='note', "
            "summarizes=[ids...]) - a compact, current statement of what is true about that file."
        ),
        "inputSchema": {"type": "object", "properties": {"min_entries": {"type": "integer", "default": 8}}},
    },
    {
        "name": "memory_stats",
        "description": "Summary of the project's memory store: counts by type/status, conflicts, store health.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# ── tool implementations ──────────────────────────────────────────────────────

# Session recall tracking: entry_id -> set of files it touches. When the agent
# later saves a memory whose files overlap a recalled entry, that entry proved
# relevant — auto-reinforce it. This closes the feedback loop that manual
# memory_confirm never closed in practice (usage_count was 0 across all stores).
_SESSION_RECALLS: Dict[str, Dict[str, set]] = {}
# +0.05 per confirmed reuse (audit #2: +0.02 x 30 events was noise against the
# daily decay pull). Combined with the usage_count half-life bonus in decay.py.
_AUTO_REINFORCE_DELTA: float = 0.05
_AUTO_REINFORCE_MAX_PER_ADD: int = 3


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
    # Remember what was recalled this session for auto-reinforcement.
    for r in results:
        files = {f.lower() for f in (r.get("files") or [])}
        funcs = {f.lower() for f in (r.get("functions") or [])}
        if files or funcs:
            _SESSION_RECALLS[r["id"]] = {"files": files, "functions": funcs}
    # Reads also keep the store healthy - maintenance must not freeze in
    # read-only phases (all hooks are daily-guarded, so this is cheap).
    try:
        eng.run_daily_maintenance()
    except Exception:
        pass
    lines = [f"Top {len(results)} memories for: {query!r}\n"]
    for r in results:
        tags = ", ".join(r.get("tags", []))
        unstable = "  ⚠ UNSTABLE (repeatedly reverted)" if "unstable" in r.get("tags", []) else ""
        lines.append(
            f"• [{r['id'][:8]}] score={r['score']} ({r.get('type')}, {r.get('status')}) "
            f"{r.get('description')}{unstable}\n"
            f"    fix: {r.get('fix_summary', '')}\n"
            f"    files: {', '.join(r.get('files', []))}"
            + (f"\n    tags: {tags}" if tags else "")
        )
    lines.append("\n(memory_confirm <id> when an entry proves right, memory_reject <id> when it is wrong; "
                 "to correct one, memory_add with supersedes=[id])")
    return "\n".join(lines)


def _file_frequencies(eng):
    """(file -> number of live entries touching it, number of live entries)."""
    counts: Dict[str, int] = {}
    n = 0
    try:
        for e in eng.list_memory():
            if e.get("status") in ("active", "conflict"):
                n += 1
                for f in set(e.get("files") or []):
                    counts[f.lower()] = counts.get(f.lower(), 0) + 1
    except Exception:
        pass
    return counts, n


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
        "supersedes": args.get("supersedes", []) or [],
        "summarizes": args.get("summarizes", []) or [],
        "task": str(args.get("task", "") or ""),
        "status": "active",
    }
    eng = _engine()
    result = eng.add_memory(payload)
    entry = result.get("entry", {})
    msg = f"✅ Saved memory {entry.get('id', '?')} ({entry.get('type')})."
    if result.get("superseded"):
        msg += "\n↪ Superseded: " + ", ".join(i[:8] for i in result["superseded"]) + " (no longer recalled)."
    if entry.get("summarizes"):
        msg += f"\n🗂 Knowledge card created over {len(entry['summarizes'])} entr(ies)."
    if "pinned" in (entry.get("tags") or []):
        msg += "\n📌 Pinned: shown at every session start, never decays."
    if result.get("revert_warning"):
        msg += "\n⚠ " + result["revert_warning"].get("message", "Revert pattern detected.")
    for m in result.get("auto_merged") or []:
        msg += (f"\n🔀 Near-duplicate of an existing memory - merged into "
                f"{m.get('merged_entry_id', '?')[:8]} (both originals superseded).")

    # Auto-reinforce recalled memories whose surface the agent just re-edited.
    # Evidence must be specific: a shared function, or a shared file that few
    # entries touch. Overlap on a hot file proves nothing - in one store 77% of
    # all entries touched a hot file, and umbrella entries with long file lists
    # collected reinforcement from every unrelated add (audit #4).
    new_files = {f.lower() for f in (payload["files"] or [])}
    new_funcs = {f.lower() for f in (entry.get("functions") or [])}
    if (new_files or new_funcs) and _SESSION_RECALLS:
        df, n_live = _file_frequencies(eng)
        rare_max = max(8, int(0.03 * n_live))
        reinforced = 0
        for eid, surf in list(_SESSION_RECALLS.items()):
            if reinforced >= _AUTO_REINFORCE_MAX_PER_ADD:
                break
            if eid == entry.get("id"):
                continue
            shared_funcs = new_funcs & surf["functions"]
            shared_rare = {f for f in (new_files & surf["files"]) if df.get(f, 0) <= rare_max}
            if shared_funcs or shared_rare:
                try:
                    eng.reinforce(
                        eid, delta=_AUTO_REINFORCE_DELTA,
                        reason="auto: recalled entry's {} re-edited this session".format(
                            "function" if shared_funcs else "file"),
                    )
                    reinforced += 1
                    del _SESSION_RECALLS[eid]  # reinforce once per session
                except Exception:
                    pass
        if reinforced:
            msg += f"\n↑ Auto-reinforced {reinforced} previously recalled memor(ies) that informed this work."

    # Input-quality nudge: a bug_fix without a root cause is far less useful
    # to future sessions. Don't block - just teach the agent.
    if payload["type"] == "bug_fix" and not (payload["cause"] or "").strip():
        msg += ("\n💡 Tip: this bug_fix has no 'cause'. Add the root cause next time - "
                "it is what makes the memory valuable when the bug resurfaces.")
    return msg


def _tool_memory_resolve(args: Dict[str, Any]) -> str:
    cid = str(args.get("id", "")).strip()
    action = str(args.get("action", "")).strip()
    if not cid or not action:
        return "Error: 'id' and 'action' are required."
    eng = _engine()
    ids = [c.get("id", "") for c in eng.list_conflicts()]
    match = [i for i in ids if i == cid] or [i for i in ids if i.startswith(cid)]
    if len(match) != 1:
        return f"Error: conflict {cid} not found (or ambiguous). Open conflicts: {', '.join(i[:8] for i in ids) or 'none'}."
    try:
        r = eng.resolve_conflict(match[0], action=action, reason=str(args.get("reason", "") or "resolved via MCP"))
    except (KeyError, ValueError) as exc:
        return f"Error: {exc}"
    return f"✅ Conflict {match[0][:8]} resolved ({action}); affected: {', '.join(i[:8] for i in r.get('affected_entries', []))}."


def _tool_memory_pin(args: Dict[str, Any]) -> str:
    eid = str(args.get("id", "")).strip()
    if not eid:
        return "Error: 'id' is required."
    try:
        r = _engine().set_pinned(eid, bool(args.get("pinned", True)))
    except KeyError:
        return f"Error: no memory with id {eid}."
    return f"📌 {r['id'][:8]} {'pinned' if r['pinned'] else 'unpinned'}."


def _tool_memory_task(args: Dict[str, Any]) -> str:
    task = str(args.get("task", "")).strip()
    if not task:
        return "Error: 'task' is required."
    items = _engine().task_timeline(task)
    if not items:
        return f"No memories mention task {task}."
    lines = [f"Task {task}: {len(items)} entr(ies), oldest first"]
    for e in items:
        lines.append(f"• {(e.get('timestamp') or '')[:10]} [{e.get('id', '?')[:8]}] ({e.get('type')}, "
                     f"{e.get('status')}) {(e.get('description') or '')[:160]}")
    return "\n".join(lines)


def _tool_memory_consolidate(args: Dict[str, Any]) -> str:
    clusters = _engine().consolidation_candidates(min_entries=int(args.get("min_entries", 8) or 8))
    if not clusters:
        return "Nothing to consolidate: no file has enough un-carded entries."
    lines = [f"{len(clusters)} cluster(s) ready for a knowledge card. For each: read the entries, then "
             "memory_add(type='note', description='CARD <file>: ...', fix='<what is currently true>', "
             "files=[file], summarizes=[all ids]).\n"]
    for c in clusters:
        lines.append(f"## {c['surface']}  ({c['count']} entries)")
        for e in c["entries"]:
            lines.append(f"- [{e['id'][:8]}] {e['timestamp']} ({e['type']}) {e['description'][:150]}")
            if e.get("fix"):
                lines.append(f"    fix: {e['fix'][:220]}")
        lines.append("  ids: " + ", ".join(e["id"][:8] for e in c["entries"]))
    return "\n".join(lines)[:24000]


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
    out = json.dumps(state, ensure_ascii=False, indent=2)
    # Loop-health nudge: a project without the SessionStart hook writes
    # memories nobody reads. Detect from the client's cwd (best-effort).
    try:
        settings = Path.cwd() / ".claude" / "settings.json"
        hooked = settings.exists() and "context_injector" in settings.read_text(encoding="utf-8")
        if not hooked:
            out += ("\n\n💡 This project has no SessionStart memory-injection hook. "
                    "Run:  python D:\\ai_memory_system\\connect.py  (from the project root) "
                    "so every new session starts with the project memory loaded.")
    except Exception:
        pass
    return out


_DISPATCH = {
    "memory_query": _tool_memory_query,
    "memory_add": _tool_memory_add,
    "memory_confirm": _tool_memory_confirm,
    "memory_reject": _tool_memory_reject,
    "memory_recent": _tool_memory_recent,
    "memory_conflicts": _tool_memory_conflicts,
    "memory_stats": _tool_memory_stats,
    "memory_resolve": _tool_memory_resolve,
    "memory_pin": _tool_memory_pin,
    "memory_task": _tool_memory_task,
    "memory_consolidate": _tool_memory_consolidate,
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
