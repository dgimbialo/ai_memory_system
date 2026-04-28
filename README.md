# AI Memory System

A self-contained, **local** persistent memory engine for software projects, built for VS Code + GitHub Copilot Agent workflows.

It **remembers decisions, tracks bugs and features, detects contradictions** between historical entries, renders a structured Markdown wiki, and — crucially — automatically feeds that knowledge back into every Copilot Agent session via VS Code hooks so the agent always has full project context without any manual `#file` references.

---

## How It Works

```
┌─────────────────────────────────────────────────────────────┐
│  VS Code  (Agent mode)                                       │
│                                                             │
│  SessionStart hook ──► context_injector.py                  │
│      └─ injects project memory summary as systemMessage     │
│                                                             │
│  Agent edits a file                                         │
│  PostToolUse hook  ──► hook_handler.py                      │
│      └─ auto-saves entry to memory + re-renders wiki        │
│                                                             │
│  copilot-instructions.md                                    │
│      └─ tells agent to run add_memory after every edit      │
└─────────────────────────────────────────────────────────────┘
         ▼
   data/projects/<name>/memory.json   (per-project, isolated)
   data/projects/<name>/wiki/         (Markdown wiki)
```

One installation at `C:\ai_memory_system` serves **all** your projects — each with isolated data under `data/projects/<name>/`.

## Features

- **SessionStart hook** — injects full project memory into every Copilot Agent session automatically
- **PostToolUse hook** — deterministically records a memory entry after every file write
- **Append-first storage** — historical entries are never silently overwritten
- **Atomic JSON writes** with automatic backup before overwrite
- **Conflict detection** via semantic similarity + heuristics:
  - contradictory fixes on shared files
  - semantically opposite solutions (`enable` vs `disable`)
  - unresolved duplicate issues
- **Markdown wiki** auto-rendered with Obsidian-friendly `[[wikilinks]]`: by type, file, status
- **Activity log** — every change timestamped with action and reason
- **Learner** — analyses Copilot chat logs, extracts patterns, updates all managed project instructions
- **Daemon** — background file watcher for non-agent edits (fallback)
- **Multi-project** — `--project name` flag isolates data per project
- **Offline-first embeddings** — uses `sentence-transformers` when installed, falls back to a deterministic local hasher

---

## Requirements

- Python 3.10+
- No mandatory third-party dependencies (pure stdlib for core functionality)

**Optional** (recommended):

```
pip install sentence-transformers   # higher-quality semantic similarity
pip install watchdog                # efficient file watching (daemon mode)
```

---

## Installation

```powershell
git clone https://github.com/your-username/ai-memory-system.git C:\ai_memory_system
cd C:\ai_memory_system

# (Optional) create venv
python -m venv .venv ; .venv\Scripts\Activate.ps1

# Bootstrap: creates data/ files
python bootstrap.py
```

---

## Quickstart — Memory CLI (`run.py`)

```powershell
# Add a memory entry for a specific project
python run.py --project my_app add_memory `
  --type bug_fix `
  --description "Login crashes on empty password" `
  --cause "Missing null-check before bcrypt call" `
  --fix "Added validation in auth.py line 42" `
  --files src/auth.py `
  --confidence 0.9 `
  --tags backend auth

# List entries for a project
python run.py --project my_app list_memory

# Semantic search
python run.py --project my_app query_memory "authentication bug"

# Detect conflicts
python run.py --project my_app detect_conflicts

# Render Markdown wiki  →  data/projects/my_app/wiki/
python run.py --project my_app render_wiki

# Health check
python run.py --project my_app lint

# Engine summary
python run.py --project my_app state
```

> Omit `--project` to use the default shared store (`data/`).

### All `run.py` commands

| Command | Description |
|---|---|
| `add_memory` | Add a structured memory entry |
| `list_memory` | List entries (filter: `--type`, `--status`) |
| `detect_conflicts` | Full conflict re-scan |
| `query_memory` | Top-k semantic search |
| `state` | Entry count, conflicts, wiki, embedding backend |
| `update_status` | Change entry status (logged) |
| `update_confidence` | Change entry confidence (logged) |
| `render_wiki` | Render Markdown wiki under `data/…/wiki/` |
| `lint` | 7 health checks: stale, low-confidence, orphans, duplicates… |

---

## VS Code + Copilot Integration (`run_infra.py`)

### 1. Scaffold a project

```powershell
python run_infra.py setup C:\Projects\MyApp `
  --language python `
  --framework django
```

Creates inside `C:\Projects\MyApp`:
```
.github/
  copilot-instructions.md   ← project standards + Agent Workflow section
  hooks/
    memory.json             ← SessionStart + PostToolUse hooks
  prompts/
    record-memory.prompt.md ← /record-memory slash-command
.vscode/
  settings.json
  extensions.json
```

### 2. The hooks (automatic, zero-effort)

**`SessionStart`** → `core/context_injector.py`

At the start of every agent session, the agent receives:
```
## Project Memory — my_app  (2026-04-28 17:31 UTC)
### Recent Active Entries (5 total)
- [abc12345] (bug_fix, conf=0.90) Login crashes on empty password → src/auth.py [2026-04-10]
### Open Conflicts (1)
- abc12345 ↔ def67890  sim=0.92  duplicate unresolved issue
### Stats  total=12  bug_fix=4  feature=5  decision=3
```

**`PostToolUse`** → `core/hook_handler.py`

After every file write the agent makes, a memory entry is automatically created:
```
[memory:my_app] ✅ a1b2c3d4 (feature) — Add login endpoint
```

### 3. Scan & learn from Copilot activity

```powershell
# One-shot scan of VS Code Copilot debug logs
python run_infra.py scan

# Watch continuously (5-second interval)
python run_infra.py watch --interval 5

# Analyse activity and update all managed project instructions
python run_infra.py learn

# Show system status
python run_infra.py status
```

### 4. Background daemon (non-agent edits)

```powershell
# Watch specific projects for file changes and auto-create memory entries
python run_infra.py daemon --projects C:\Projects\MyApp C:\Projects\OtherApp
```

---

## Folder Structure

```
ai_memory_system/
├── core/
│   ├── engine.py            # single gateway — all memory operations
│   ├── storage.py           # atomic JSON I/O + backups
│   ├── models.py            # MemoryEntry, ConflictRecord
│   ├── conflict.py          # conflict detection rules
│   ├── embeddings.py        # sentence-transformers + fallback
│   ├── updater.py           # controlled merge & wiki rebuild
│   ├── wiki_md.py           # Markdown wiki renderer (Obsidian wikilinks)
│   ├── lint.py              # 7 health checks
│   ├── vscode_infra.py      # scaffolds .github/ in any project
│   ├── copilot_logger.py    # reads VS Code Copilot debug logs
│   ├── learner.py           # extracts patterns, updates instructions
│   ├── auto_memory.py       # correlates file changes + Copilot events
│   ├── watcher.py           # file watcher daemon (watchdog / polling)
│   ├── hook_handler.py      # PostToolUse hook — auto memory recording
│   └── context_injector.py  # SessionStart hook — memory context injection
├── data/                    # ← gitignored; created by bootstrap.py
│   ├── .gitkeep
│   └── projects/            # per-project isolated stores
│       └── <name>/
│           ├── memory.json
│           ├── conflicts.json
│           └── wiki/
├── templates/
│   └── copilot_instructions.md.tpl
├── .github/
│   ├── copilot-instructions.md
│   ├── hooks/
│   │   └── memory.json      # SessionStart + PostToolUse hooks
│   └── prompts/
│       └── record-memory.prompt.md
├── bootstrap.py
├── run.py                   # memory CLI
├── run_infra.py             # VS Code / Copilot infra CLI
└── README.md
```

---

## Memory Entry Schema

```json
{
  "id": "abc123def456",
  "type": "bug_fix | feature | note | decision",
  "description": "string (required)",
  "cause": "what triggered the change",
  "fix": "what exactly changed and where",
  "files": ["relative/path/to/file.py"],
  "status": "active | resolved | conflict",
  "confidence": 0.9,
  "timestamp": "2026-04-28T17:00:00+00:00",
  "conflicts_with": ["other_entry_id"],
  "tags": ["agent", "auto", "project:my_app"]
}
```

---

## Update Policy

| Allowed | Not Allowed |
|---|---|
| Add new entries | Delete historical entries |
| Update `status`, `confidence` | Silent rewrite of past memory |
| Mark conflicts (append-only) | Modifications outside `MemoryEngine` |

Every change is recorded in `activity_log.json` with timestamp, action, affected IDs, and reason.

---

## Programmatic Use

```python
from core.engine import MemoryEngine

engine = MemoryEngine("data/projects/my_app")
result = engine.add_memory({
    "type": "decision",
    "description": "Use PostgreSQL for primary store",
    "fix": "Added pg driver and migrations",
    "files": ["db/migrations/0001.sql"],
    "confidence": 0.95,
})
# result["entry"]     → the saved entry
# result["conflicts"] → any conflicts detected
```

---

## License

MIT
