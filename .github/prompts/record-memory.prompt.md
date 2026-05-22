---
mode: agent
description: >
  Record a memory entry for the current code change. Use when you want to
  manually save a decision, bug fix, or feature to the AI Memory System.
  Triggers: "record memory", "save to memory", "add memory entry", "log this change".
---

Record a memory entry for the change just made.

**Steps:**
1. Determine the correct `--type`:
   - `bug_fix` — fixing an error or crash
   - `feature` — adding new functionality
   - `decision` — choosing between alternatives
   - `note` — anything else

2. Run in the terminal:

```powershell
c:/python313/python.exe C:/ai_memory_system/run.py add_memory `
  --type {{type}} `
  --description "{{description}}" `
  --cause "{{cause}}" `
  --fix "{{fix}}" `
  --files {{files}} `
  --confidence 0.8 `
  --tags agent manual
```

3. Then refresh the wiki (safe to call every time — skips automatically if already up to date):

```powershell
c:/python313/python.exe C:/ai_memory_system/run.py render_wiki
```

4. Confirm by showing the entry ID from the command output.
