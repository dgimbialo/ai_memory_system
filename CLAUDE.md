# AI Memory System — Claude Code guide

## Slash commands (available everywhere)

All `/mem*` commands are global — they work in **any** project chat, not just here.
The project is resolved automatically from the current working directory.

| Command | What it does |
|---------|-------------|
| `/mem <topic>` | **Search** memory before editing code |
| `/memadd <type> <desc> [options]` | **Save** a fix/decision/feature |
| `/memrecent` | Latest entries |
| `/memlist [--type] [--status] [--file]` | Filter entries |
| `/memconfirm <id>` | Mark a memory as correct (↑ confidence) |
| `/memreject <id>` | Mark a memory as wrong (↓ confidence) |
| `/memsession` | Save a session summary |
| `/memconflicts` | Unresolved contradictions |
| `/memstats` | Store health |
| `/memlint` | Integrity check |
| `/memdecay [--apply]` | Apply confidence decay |
| `/memdedup [--apply]` | Merge near-duplicates |
| `/memrecompute [--apply]` | Clear false-positive unstable tags |
| `/memwiki` | Render Markdown wiki |
| `/memui [--port N]` | Open **web dashboard** in browser |

## Workflow

1. Start a task → `/mem <what you're about to touch>` to recall past context.
2. Finish a fix → `/memadd bug_fix "what happened" --fix "what changed" --files "..."`
3. End of session → `/memsession` to create a searchable session summary.
4. Realise a memory was wrong → `/memreject <id>`.
5. Weekly → `/memdedup --apply` and `/memdecay --apply`.

## Project data location
`D:\ai_memory_system\data\projects\<slug>\`

## Connect a new project to all IDEs
```
cd <project>
python D:\ai_memory_system\connect.py
```
