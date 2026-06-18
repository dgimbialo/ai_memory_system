# One-command integration (Claude Code · Cursor · VS Code)

The memory system plugs into AI agents through a single **MCP server**
(`mcp_server.py`). MCP is the protocol that Claude Code, Cursor and VS Code
(Copilot/Continue) all speak, so one server file covers every IDE — no plugins.

## Connect a project

From inside the project you want to give memory to:

```bash
python D:\ai_memory_system\connect.py
```

Or point it at any folder:

```bash
python D:\ai_memory_system\connect.py C:\Path\To\Project
```

That's the whole setup. `connect.py`:

1. Creates the per-project store at `data/projects/<slug>/`.
2. Auto-detects which IDEs you have and writes/merges their MCP config:
   - Claude Code → `<project>/.mcp.json`
   - Cursor → `<project>/.cursor/mcp.json`
   - VS Code → `<project>/.vscode/mcp.json` (+ Copilot instructions/hooks)
3. Uses the repo `.venv` interpreter so semantic search deps are available.

Existing config is **merged**, never overwritten. Re-running is safe.

After it finishes, **reload the IDE**. The agent gains five tools:

| Tool | Use |
|------|-----|
| `memory_query` | Semantic recall before editing code |
| `memory_add` | Persist a fix/decision/feature |
| `memory_recent` | Latest active entries |
| `memory_conflicts` | Unresolved contradictions |
| `memory_stats` | Store health summary |

## Options

```
python connect.py [path] [--ide claude|cursor|vscode]... [--no-copilot] [--json]
```

- `--ide` forces specific targets (repeatable); default is auto-detect.
- `--no-copilot` skips the VS Code Copilot scaffolding.
- `--json` prints a machine-readable result.

## How project resolution works

`mcp_server.py` picks its store from, in order: `AI_MEMORY_DATA_DIR` env →
`AI_MEMORY_PROJECT` env (slug) → current workspace folder name → default store.
`connect.py` writes `AI_MEMORY_PROJECT=<slug>` into each IDE's config, so the
right store is always selected automatically.

## Manual smoke test

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  | AI_MEMORY_PROJECT=piobmasterpro .venv/Scripts/python.exe mcp_server.py
```
