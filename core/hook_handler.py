"""PostToolUse hook handler for AI Memory System.

VS Code calls this script after every file-write tool invocation.
Input (stdin): JSON with tool name and tool input.
Output (stdout): JSON to control hook behavior.

Responsibilities
----------------
1. Parse the tool input to extract the changed file path + description.
2. Look up the most recent Copilot chat event within a correlation window.
3. Auto-create a memory entry via MemoryEngine.
4. Re-render the markdown wiki.
5. Return a non-blocking JSON response so the agent continues normally.

Exit codes
----------
0  — success (non-blocking, agent continues)
2  — blocking error (agent stops — never used here; we always allow)
"""
from __future__ import annotations

import json
import sys
import re
from pathlib import Path

# Ensure project root is on sys.path regardless of cwd
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "data"

# File tools that modify the workspace (Copilot / VS Code agent tools)
_WRITE_TOOLS = {
    "write_file",
    "create_file",
    "replace_string_in_file",
    "str_replace_in_file",
    "multi_replace_string_in_file",
    "edit_file",
    "apply_edit",
    "overwrite_file",
    "insert_edit_into_file",
}

# Extensions worth tracking (mirrors auto_memory.py)
_TRACKED_EXT = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".css", ".scss", ".html",
    ".java", ".cs", ".go", ".rs", ".rb", ".swift", ".kt",
    ".json", ".yaml", ".yml", ".toml",
    ".sh", ".ps1", ".md",
}

# Paths to silently ignore
_IGNORED_PARTS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", "dist", "build",
    "data" + "/" + "wiki",   # our own output — avoid recursive writes
    "data\\wiki",
}


def _is_tracked(file_path: str) -> bool:
    p = Path(file_path)
    if p.suffix.lower() not in _TRACKED_EXT:
        return False
    parts_str = file_path.replace("\\", "/")
    for ignored in _IGNORED_PARTS:
        if ignored in parts_str:
            return False
    return True


def _extract_file_path(tool_name: str, tool_input: dict) -> str | None:
    """Pull the target file path from the tool input dict."""
    for key in ("filePath", "path", "file_path", "target", "file", "uri"):
        val = tool_input.get(key)
        if isinstance(val, str) and val:
            return val
    # multi_replace_string_in_file: replacements[0].filePath
    replacements = tool_input.get("replacements")
    if isinstance(replacements, list) and replacements:
        fp = replacements[0].get("filePath")
        if fp:
            return fp
    return None


def _extract_description(tool_name: str, tool_input: dict) -> str:
    """Best-effort human-readable description of the change."""
    # Prefer an explicit explanation field
    for key in ("explanation", "description", "comment", "reason"):
        val = tool_input.get(key)
        if isinstance(val, str) and len(val) > 5:
            return val.strip()[:200]
    # For replace tools use the new content snippet
    new_str = tool_input.get("newString") or tool_input.get("new_string", "")
    if new_str and len(new_str) > 5:
        first_line = new_str.strip().splitlines()[0][:100]
        return f"{tool_name}: {first_line}"
    return f"File edited via {tool_name}"


_TYPE_HINTS = [
    (r"\b(fix|bug|error|crash|exception|issue|broken|fail)\b", "bug_fix"),
    (r"\b(add|implement|creat|build|feature|support|new)\b", "feature"),
    (r"\b(decid|choos|switch|migrat|replac|move)\b", "decision"),
]


def _infer_type(description: str) -> str:
    for pattern, entry_type in _TYPE_HINTS:
        if re.search(pattern, description, re.IGNORECASE):
            return entry_type
    return "note"


def _find_recent_copilot_prompt() -> str:
    """Return the text of the most recent user Copilot chat message, or ''."""
    try:
        from core.auto_memory import _find_recent_copilot_event, _extract_user_prompt
        from datetime import datetime, timezone
        event = _find_recent_copilot_event(DATA_DIR, datetime.now(timezone.utc))
        if event:
            return _extract_user_prompt(event)
    except Exception:
        pass
    return ""


def _ok(message: str = "") -> None:
    """Print a non-blocking success response and exit 0."""
    out = {"continue": True}
    if message:
        out["systemMessage"] = message
    print(json.dumps(out))
    sys.exit(0)


def _detect_project(file_path: str) -> str | None:
    """
    Detect the project name from the file path.

    Rules (highest priority first):
    1. If the file is inside ROOT (ai_memory_system itself) → None (default store).
    2. Otherwise take the top-level directory name relative to the drive root,
       e.g. C:\\my_project\\src\\main.py  → "my_project".
    """
    p = Path(file_path).resolve()
    try:
        p.relative_to(ROOT)
        return None  # file belongs to the memory system itself
    except ValueError:
        pass
    # Walk up to find the top-level folder (2 levels above drive root)
    parts = p.parts  # ('C:\\', 'my_project', 'src', 'main.py')
    if len(parts) >= 2:
        return parts[1]  # 'my_project'
    return None


def _project_data_dir(project: str | None) -> Path:
    if project:
        slug = project.strip().lower().replace(" ", "_")
        return ROOT / "data" / "projects" / slug
    return DATA_DIR


def _run(tool_name: str, tool_input: dict, tool_response: dict) -> None:
    if tool_name not in _WRITE_TOOLS:
        _ok()
        return

    file_path = _extract_file_path(tool_name, tool_input)
    if not file_path or not _is_tracked(file_path):
        _ok()
        return

    description = _extract_description(tool_name, tool_input)

    # Enrich with Copilot chat context if available
    prompt = _find_recent_copilot_prompt()
    if prompt:
        first_sentence = re.split(r"[.\n]", prompt.strip())[0].strip()
        if len(first_sentence) > 10:
            description = first_sentence[:200]

    entry_type = _infer_type(description)

    # Detect project from file location
    project = _detect_project(file_path)
    data_dir = _project_data_dir(project)

    # Relative file path (strip absolute prefix if possible)
    try:
        rel = str(Path(file_path).relative_to(ROOT))
    except ValueError:
        # File is outside ROOT — make relative to its project root
        p = Path(file_path).resolve()
        if project and len(p.parts) >= 2:
            try:
                import os
                project_root = Path(p.parts[0]) / p.parts[1]
                rel = str(p.relative_to(project_root))
            except ValueError:
                rel = file_path
        else:
            rel = file_path

    tags = ["agent", "auto"]
    if project:
        tags.append(f"project:{project.lower()}")

    payload = {
        "type": entry_type,
        "description": description,
        "cause": "Copilot agent edit" + (f" — {prompt[:100]}" if prompt else ""),
        "fix": f"File written via {tool_name}: {Path(file_path).name}",
        "files": [rel],
        "status": "active",
        "confidence": 0.75 if prompt else 0.55,
        "tags": tags,
    }

    try:
        from core.engine import MemoryEngine
        engine = MemoryEngine(data_dir)
        result = engine.add_memory(payload)
        eid = result["entry"]["id"]

        # Refresh wiki silently
        try:
            engine.render_wiki_md()
        except Exception:
            pass

        _ok(f"[memory:{project or 'default'}] ✅ {eid} ({entry_type}) — {description[:60]}")
    except ValueError as exc:
        # Duplicate id or validation error — non-fatal
        _ok(f"[memory] skipped: {exc}")
    except Exception as exc:
        # Never block the agent
        _ok(f"[memory] ⚠️ error (non-blocking): {exc}")


def main() -> None:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        _ok()
        return

    tool_name = data.get("toolName", "")
    tool_input = data.get("toolInput") or {}
    tool_response = data.get("toolResponse") or {}

    _run(tool_name, tool_input, tool_response)


if __name__ == "__main__":
    main()
